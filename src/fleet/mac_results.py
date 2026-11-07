"""Pull the nightly Mac platform pass's screenshots and deliver them to the owner's chat.

The Mac runner writes ``~/mac-nightly-results/<repo>/<run id>/`` (a ``READY``
file marks a complete run). One pass of this module lists those runs over the
read-only forced-command SSH key (``mac/bin/hub-results-pull.sh``), copies the
``*.png`` files of each new run, hands them to ``send_file.py`` and records the
run as delivered only after every send is confirmed. Nothing is written back to
the Mac. Setup and the forced-command key: ``docs/mac-results.md``.

The run folder is data produced by adopter code, so every value that crosses
SSH is re-validated here: names and ids against strict patterns before a
remote command is built, listing entries against a type/size/count gate, and
the received bytes against the declared size and the PNG signature. A run that
fails a gate is refused whole and says why; it is never partly delivered.

Each outcome is its own state, so "could not read the Mac", "no READY yet",
"refused", "not confirmed" and "nothing new" never collapse into one another.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ..host_profile import get_host
from ..no_window import NO_WINDOW
from . import mac_results_store as store
from . import remote_stats
from .ssh_exec import SshResult, run_ssh

RESULTS_KEY_ENV = "LOCAL_LLM_HUB_RESULTS_SSH_KEY"
STAGING_ENV = "MAC_RESULTS_STAGING"

# The Mac prunes at 14 days; a window well inside that means a run is never
# being deleted while it is pulled.
DEFAULT_WINDOW_DAYS = 4
MAX_RUNS_PER_PASS = 10
# Per-run gates: the hostile-folder bound. Mirrors MAX_BYTES in the Mac script
# (which only bounds its own read) and sits under the launcher's 15 MB cap.
MAX_FILES_PER_RUN = 20
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_RUN_BYTES = 40 * 1024 * 1024

_SSH_LIST_TIMEOUT_S = 30
_SSH_GET_TIMEOUT_S = 90
_SEND_TIMEOUT_S = 60

_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RUN_RE = re.compile(r"^[0-9]{1,20}$")
_REL_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+){0,3}\.png$")
_OUTCOME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_REGULAR = "Regular File"

# Outcome states (one per run, per pass).
DELIVERED = "delivered"
PENDING = "pending"  # no READY yet
REFUSED = "refused"  # a gate failed; says why
NOT_CONFIRMED = "not_confirmed"  # a copy or a send could not be confirmed
EMPTY = "empty"  # READY, and the Mac confirms there is no image to send
WOULD_DELIVER = "would_deliver"  # --dry-run only


@dataclass(frozen=True)
class RunEntry:
    repo: str
    run_id: str
    ready: bool
    age_s: int
    e2e: str
    smoke: str


@dataclass(frozen=True)
class RunOutcome:
    repo: str
    run_id: str
    state: str
    detail: str = ""


@dataclass
class PassReport:
    """What one pass saw. ``list_state`` is ``ok`` only when the Mac's listing
    was read and parsed; otherwise it says which step failed."""

    list_state: str = "ok"
    list_detail: str = ""
    outcomes: List[RunOutcome] = field(default_factory=list)
    already_delivered: int = 0
    out_of_window: int = 0

    @property
    def exit_code(self) -> int:
        """0 = every run the pass met is delivered or legitimately waiting;
        1 = at least one thing is unknown or refused (the next pass retries)."""
        if self.list_state != "ok":
            return 1
        return 1 if any(o.state in (REFUSED, NOT_CONFIRMED) for o in self.outcomes) else 0


@dataclass(frozen=True)
class SendOutcome:
    kind: str  # "sent" | "refused" | "unavailable"
    detail: str = ""


Ssh = Callable[[str, int], SshResult]
Sender = Callable[[Path, str], SendOutcome]


# ------------------------------------------------------------------ listing


def _ssh_failure(result: SshResult) -> str:
    """A fixed-vocabulary reason: ssh's own stderr can carry the address and
    login, which never go into a log or a chat."""
    if result.error is not None:
        return "ssh could not run or timed out"
    if result.returncode == 255:
        return "ssh could not connect or authenticate"
    return f"remote command exited {result.returncode}"


def parse_listing(text: str) -> Tuple[List[RunEntry], List[str]]:
    """``(runs, malformed)`` from the script's ``list`` output. A line that does
    not match the grammar is reported, not trusted and not fatal."""
    runs: List[RunEntry] = []
    malformed: List[str] = []
    now: Optional[int] = None
    for line in text.splitlines():
        parts = line.split(" ")
        if len(parts) == 2 and parts[0] == "NOW" and parts[1].isdigit():
            now = int(parts[1])
        elif len(parts) == 7 and parts[0] == "RUN" and now is not None:
            _, repo, run_id, kind, stamp, e2e, smoke = parts
            if not (_REPO_RE.match(repo) and _RUN_RE.match(run_id) and kind in ("ready", "pending") and stamp.isdigit()):
                malformed.append("RUN line failed validation")
                continue
            runs.append(RunEntry(
                repo, run_id, kind == "ready", max(0, now - int(stamp)),
                e2e if _OUTCOME_RE.match(e2e) else "unknown",
                smoke if _OUTCOME_RE.match(smoke) else "unknown",
            ))
        elif line.strip():
            malformed.append("unparseable listing line")
    return runs, malformed


@dataclass(frozen=True)
class RemoteImage:
    rel: str
    size: int


class Refusal(Exception):
    """A gate failed; the message is the reason shown for the whole run."""


def parse_files(text: str) -> List[RemoteImage]:
    """The run's ``*.png`` entries, gated. Raises :class:`Refusal` on the first
    thing that makes the folder untrustworthy: a link or other non-regular
    entry, an odd name, an oversized file, too many files, too many bytes."""
    images: List[RemoteImage] = []
    total = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        kind, sep, rest = line.partition("|")
        size_text, sep2, rel = rest.partition("|")
        if not sep or not sep2 or not size_text.isdigit():
            raise Refusal("a listing line could not be parsed")
        if kind != _REGULAR:
            raise Refusal("a png entry is not a regular file (link or other)")
        if not _REL_RE.match(rel) or ".." in rel:
            raise Refusal("a png has a name outside the allowed pattern")
        size = int(size_text)
        if size > MAX_FILE_BYTES:
            raise Refusal(f"a png is over the {MAX_FILE_BYTES // (1024 * 1024)} MiB file cap")
        total += size
        images.append(RemoteImage(rel, size))
        if len(images) > MAX_FILES_PER_RUN:
            raise Refusal(f"more than {MAX_FILES_PER_RUN} pngs in the run")
    if total > MAX_RUN_BYTES:
        raise Refusal(f"pngs total over the {MAX_RUN_BYTES // (1024 * 1024)} MiB run cap")
    return sorted(images, key=lambda i: i.rel)


def decode_image(output: str, expected_size: int) -> bytes:
    """The file's bytes from the script's base64, or :class:`Refusal` when
    they are not the file that was listed (partial copy, changed under us)."""
    try:
        data = base64.b64decode("".join(output.split()), validate=True)
    except (binascii.Error, ValueError):
        raise Refusal("a copied file was not valid base64") from None
    if len(data) != expected_size:
        raise Refusal("a copied file did not match its listed size")
    if not data.startswith(_PNG_MAGIC):
        raise Refusal("a copied file is not a png")
    return data


# ----------------------------------------------------------------- delivery


def make_send_file_sender(script: Path) -> Sender:
    """A :data:`Sender` that runs fleet-config's ``send_file.py`` and reads its
    one result line (``SENT=`` / ``REFUSED=`` / ``UNAVAILABLE=``)."""

    def send(path: Path, caption: str) -> SendOutcome:
        try:
            result = subprocess.run(
                [sys.executable, str(script), str(path), "--caption", caption],
                capture_output=True, text=True, timeout=_SEND_TIMEOUT_S, creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return SendOutcome("unavailable", f"send_file.py gave no answer in {_SEND_TIMEOUT_S}s")
        except OSError as exc:
            return SendOutcome("unavailable", f"send_file.py could not run ({type(exc).__name__})")
        return parse_send_result(result.stdout)

    return send


def parse_send_result(stdout: str) -> SendOutcome:
    """Only a line starting ``SENT=`` counts as delivered; anything unreadable
    is ``unavailable`` so the run is retried."""
    for line in reversed([ln.strip() for ln in stdout.splitlines() if ln.strip()]):
        if line.startswith("SENT="):
            return SendOutcome("sent", line)
        if line.startswith("REFUSED="):
            return SendOutcome("refused", line[:200])
        if line.startswith("UNAVAILABLE="):
            return SendOutcome("unavailable", line[:200])
    return SendOutcome("unavailable", "send_file.py printed no result line")


def caption_for(entry: RunEntry, rel: str, index: int, total: int) -> str:
    """Short caption: repo, run id, leg and its pass/fail."""
    leg = rel.split("/", 1)[0]
    if leg == "ios":
        outcome = entry.smoke
    elif leg == "e2e":
        outcome = entry.e2e
    else:
        leg, outcome = "other", "unknown"
    return f"{entry.repo} · run {entry.run_id} · {leg} {outcome} · {index}/{total}"


def _staged_name(index: int, rel: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", rel.rsplit(".png", 1)[0].replace("/", "__"))
    return f"{index:02d}-{stem[-100:]}.png"


def deliver_run(entry: RunEntry, ssh: Ssh, send: Sender, conn, staging_root: Path) -> RunOutcome:
    repo, run_id = entry.repo, entry.run_id
    listed = ssh(f"files {repo} {run_id}", _SSH_LIST_TIMEOUT_S)
    if not listed.ok:
        return RunOutcome(repo, run_id, NOT_CONFIRMED, f"listing the run's images failed: {_ssh_failure(listed)}")
    try:
        images = parse_files(listed.stdout)
    except Refusal as exc:
        return RunOutcome(repo, run_id, REFUSED, str(exc))
    if not images:
        store.mark_run_delivered(conn, repo, run_id, 0)
        return RunOutcome(repo, run_id, EMPTY, "READY and no png in the run")

    staging = staging_root / f"{repo}-{run_id}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        return _copy_and_send(entry, images, ssh, send, conn, staging)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _copy_and_send(entry: RunEntry, images: List[RemoteImage], ssh: Ssh, send: Sender, conn, staging: Path) -> RunOutcome:
    repo, run_id = entry.repo, entry.run_id
    sent_now = 0
    for index, image in enumerate(images, start=1):
        if store.file_sent(conn, repo, run_id, image.rel):
            continue
        got = ssh(f"get {repo} {run_id} {image.rel}", _SSH_GET_TIMEOUT_S)
        if not got.ok:
            return RunOutcome(repo, run_id, NOT_CONFIRMED, f"copying a file failed: {_ssh_failure(got)}")
        try:
            data = decode_image(got.stdout, image.size)
        except Refusal as exc:
            return RunOutcome(repo, run_id, REFUSED, str(exc))
        path = staging / _staged_name(index, image.rel)
        path.write_bytes(data)
        outcome = send(path, caption_for(entry, image.rel, index, len(images)))
        if outcome.kind != "sent":
            return RunOutcome(repo, run_id, NOT_CONFIRMED, f"send {outcome.kind}: {outcome.detail}")
        store.mark_file_sent(conn, repo, run_id, image.rel)
        sent_now += 1
    store.mark_run_delivered(conn, repo, run_id, len(images))
    return RunOutcome(repo, run_id, DELIVERED, f"{sent_now} sent now, {len(images)} in the run")


def run_pass(ssh: Ssh, send: Sender, conn, staging_root: Path, *, window_days: int = DEFAULT_WINDOW_DAYS,
             dry_run: bool = False) -> PassReport:
    """One pull-and-deliver pass over the Mac's results folder."""
    report = PassReport()
    listed = ssh("list", _SSH_LIST_TIMEOUT_S)
    if not listed.ok:
        report.list_state, report.list_detail = "unreachable", _ssh_failure(listed)
        return report
    runs, malformed = parse_listing(listed.stdout)
    for reason in malformed:
        report.outcomes.append(RunOutcome("-", "-", REFUSED, reason))
    window_s = window_days * 86400
    attempted = 0
    for entry in sorted(runs, key=lambda r: r.age_s):
        if entry.age_s > window_s:
            report.out_of_window += 1
        elif store.run_delivered(conn, entry.repo, entry.run_id):
            report.already_delivered += 1
        elif not entry.ready:
            report.outcomes.append(RunOutcome(entry.repo, entry.run_id, PENDING, "no READY yet"))
        elif dry_run:
            report.outcomes.append(RunOutcome(entry.repo, entry.run_id, WOULD_DELIVER, "READY, not yet delivered"))
        elif attempted >= MAX_RUNS_PER_PASS:
            report.outcomes.append(RunOutcome(entry.repo, entry.run_id, PENDING, "left for the next pass (per-pass cap)"))
        else:
            attempted += 1
            report.outcomes.append(deliver_run(entry, ssh, send, conn, staging_root))
    return report


# ---------------------------------------------------------------------- CLI


def _default_send_file() -> Path:
    # fleet-config is a sibling checkout of this repo (or of its -wt worktree).
    return Path(__file__).resolve().parents[3] / "fleet-config" / "skills" / "_lib" / "send_file.py"


def _staging_root() -> Path:
    override = os.environ.get(STAGING_ENV, "").strip()
    # The launcher only accepts a file under its session folder or %TEMP%/claude.
    return Path(override) if override else Path(tempfile.gettempdir()) / "claude" / "mac-results"


def _ssh_for(host_id: str) -> Tuple[Optional[Ssh], str]:
    """``(ssh callable, "")`` for the host over the results key, or
    ``(None, reason)`` when the host or key is not configured."""
    key = os.environ.get(RESULTS_KEY_ENV, "").strip()
    if not key:
        return None, f"{RESULTS_KEY_ENV} is not set in .env"
    host = get_host(host_id)
    if host is None or not host.ssh_user:
        return None, f"host {host_id!r} has no ssh_user configured"
    address = remote_stats.dial_address(host, wait=True)
    if not address:
        return None, f"host {host_id!r} has no address configured"

    def ssh(command: str, timeout: int) -> SshResult:
        return run_ssh(f"{host.ssh_user}@{address}", command, key_path=key, timeout=timeout)

    return ssh, ""


def _print_report(report: PassReport) -> None:
    if report.list_state != "ok":
        print(f"❌ Mac results not read ({report.list_state}): {report.list_detail}")
        return
    icons = {DELIVERED: "✅", EMPTY: "ℹ️", PENDING: "ℹ️", WOULD_DELIVER: "ℹ️", REFUSED: "❌", NOT_CONFIRMED: "⚠️"}
    for o in report.outcomes:
        print(f"{icons[o.state]} {o.repo} run {o.run_id}: {o.state} - {o.detail}")
    print(f"ℹ️ already delivered: {report.already_delivered}; outside the pull window: {report.out_of_window}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Pull the nightly Mac screenshots and deliver them to the owner's chat.")
    parser.add_argument("--host", required=True, help="machine id of the Mac runner host (config/machines.local.yaml)")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--dry-run", action="store_true", help="list only: no copy, no send, nothing recorded")
    parser.add_argument("--send-file", default=None, help="path to send_file.py (default: the fleet-config checkout beside this repo)")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    except ImportError:
        pass
    ssh, problem = _ssh_for(args.host)
    if ssh is None:
        print(f"❌ {problem}")
        return 2
    send_file = Path(args.send_file) if args.send_file else _default_send_file()
    if not send_file.is_file():
        print("❌ send_file.py not found; pass --send-file")
        return 2
    conn = store.connect()
    try:
        report = run_pass(ssh, make_send_file_sender(send_file), conn, _staging_root(),
                          window_days=args.window_days, dry_run=args.dry_run)
    finally:
        conn.close()
    _print_report(report)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
