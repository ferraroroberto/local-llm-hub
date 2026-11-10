"""Pull the nightly Mac platform pass's screenshots and deliver them to the owner's chat.

The Mac runner writes ``~/mac-nightly-results/<repo>/<run id>/`` (a ``READY``
file marks a complete run). One pass of this module lists those runs over the
read-only forced-command SSH key (``mac/bin/hub-results-pull.sh``), copies the
``*.png`` files of each new run, hands them to fleet-config's send-file script and records the
run as delivered only after every send is confirmed. A run whose ``READY`` says
``audit=miss`` also has its ``audit.json`` copied and handed to project-scaffolding's
``routing_audit.py file``, which owns the adopter issue (the Mac holds no ``gh``).
Nothing is written back to the Mac. Setup and the forced-command key: ``docs/mac-results.md``.

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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ..fsio.runtime_files import copy_file, make_dirs, save_bytes, save_text
from ..host_profile import get_host
from ..no_window import NO_WINDOW
from . import mac_results_store as store
from . import nightly_ci, nightly_ci_issues, remote_stats
from .ssh_exec import SshResult, run_ssh

RESULTS_KEY_ENV = "LOCAL_LLM_HUB_RESULTS_SSH_KEY"
STAGING_ENV = "MAC_RESULTS_STAGING"
HOST_ENV = "MAC_RESULTS_HOST"
STATUS_ENV = "MAC_RESULTS_STATUS_FILE"
# The launcher sets JOB_ARTIFACT_DIR for every Job run; a Job is not a hosted
# session, so any APP_LAUNCHER_SESSION_ID it carries was inherited from whatever
# started the launcher and may name an unrelated live chat.
JOB_ARTIFACT_ENV = "JOB_ARTIFACT_DIR"
SESSION_ENV = "APP_LAUNCHER_SESSION_ID"

# The Mac prunes at 14 days; a window well inside that means a run is never
# being deleted while it is pulled.
DEFAULT_WINDOW_DAYS = 4
MAX_RUNS_PER_PASS = 10
# Per-run gates: the hostile-folder bound. Mirrors MAX_BYTES in the Mac script
# (which only bounds its own read) and sits under the launcher's 15 MB cap.
MAX_FILES_PER_RUN = 20
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_RUN_BYTES = 40 * 1024 * 1024
# Mirrors MAX_AUDIT_BYTES in the Mac script; routing_audit.py's own cap (1 MB) is looser.
MAX_AUDIT_BYTES = 256 * 1024

_SSH_LIST_TIMEOUT_S = 30
_SSH_GET_TIMEOUT_S = 90
_SEND_TIMEOUT_S = 60
# routing_audit.py file makes up to four gh calls of 60 s each.
_FILE_TIMEOUT_S = 300

_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RUN_RE = re.compile(r"^[0-9]{1,20}$")
_REL_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+){0,3}\.png$")
_OUTCOME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_FILE_LINE_RE = re.compile(r"^FILE=(none|created|commented|unchanged|unknown)\b(.*)$")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_REGULAR = "Regular File"

# What READY records for the routing audit. Anything but clean or miss (the
# "unknown" word, a missing line, "not run") is the audit not having measured the night.
AUDIT_CLEAN = "clean"
AUDIT_MISS = "miss"
AUDIT_UNKNOWN = "unknown"
# READY has no audit line at all: written before the audit existed, so it can never be measured.
AUDIT_LEGACY = "legacy"

# Outcome states (one per run, per pass).
DELIVERED = "delivered"
PENDING = "pending"  # no READY yet
REFUSED = "refused"  # a gate failed; says why
NOT_CONFIRMED = "not_confirmed"  # a copy or a send could not be confirmed
EMPTY = "empty"  # READY, and the Mac confirms there is no image to send
WOULD_DELIVER = "would_deliver"  # --dry-run only
AUDIT_FILED = "audit_filed"  # the filing tool confirmed an outcome (FILE=created|commented|unchanged)
AUDIT_NOT_MEASURED = "audit_unknown"  # the audit was not measured, or the filing was not confirmed
WOULD_FILE = "would_file"  # --dry-run only
LEGACY = "legacy"  # a folder that predates the audit or the READY marker; can never become measurable

# A run folder with no READY this long after its last write is not in progress (a run
# finishes within hours and the workflow writes READY last), so READY will never come.
STALE_PENDING_S = 24 * 3600


@dataclass(frozen=True)
class RunEntry:
    repo: str
    run_id: str
    ready: bool
    age_s: int
    e2e: str
    smoke: str
    audit: str = AUDIT_UNKNOWN


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
    ci_outcomes: List[nightly_ci.CiOutcome] = field(default_factory=list)

    @property
    def attention(self) -> int:
        """How many things are unknown or refused (the next pass retries): runs refused,
        not confirmed or unmeasured, and the nightly-CI stage's unknown, refused and
        unsent-digest states. A ``legacy`` folder is not one of them."""
        bad = (REFUSED, NOT_CONFIRMED, AUDIT_NOT_MEASURED)
        ci_bad = (nightly_ci.UNKNOWN, nightly_ci.REFUSED, nightly_ci.DIGEST_NOT_CONFIRMED)
        return sum(o.state in bad for o in self.outcomes) + sum(o.state in ci_bad for o in self.ci_outcomes)

    @property
    def legacy(self) -> int:
        return sum(o.state == LEGACY for o in self.outcomes)

    @property
    def exit_code(self) -> int:
        """0 = every run the pass met is delivered, legacy or legitimately waiting;
        1 = the Mac could not be read, or something is unknown or refused."""
        return 1 if self.list_state != "ok" or self.attention else 0


@dataclass(frozen=True)
class SendOutcome:
    # "kept": not delivered, but the image is kept where the owner can see it,
    # so the rest of the run is still processed.
    kind: str  # "sent" | "refused" | "unavailable" | "kept"
    detail: str = ""


@dataclass(frozen=True)
class FileOutcome:
    state: str  # the FILE= word: none | created | commented | unchanged | unknown
    detail: str = ""


Ssh = Callable[[str, int], SshResult]
Sender = Callable[[Path, str], SendOutcome]
Filer = Callable[[Path], FileOutcome]


# ------------------------------------------------------------------ listing


def _ssh_failure(result: SshResult) -> str:
    """A fixed-vocabulary reason: ssh's own stderr can carry the address and
    login, which never go into a log or a chat."""
    if result.error is not None:
        return "ssh could not run or timed out"
    if result.returncode == 255:
        return "ssh could not connect or authenticate"
    return f"remote command exited {result.returncode}"


def _audit_word(fields: List[str]) -> str:
    """The audit state of a RUN line's optional eighth field. The dispatcher prints ``-``
    for a READY with no audit line at all, which only a pre-audit workflow wrote (the
    current one always records ``unknown`` or ``not-run``). A missing field means an old
    dispatcher, which says nothing about the folder, so it stays unmeasured."""
    if not fields:
        return AUDIT_UNKNOWN
    if fields[0] in (AUDIT_CLEAN, AUDIT_MISS):
        return fields[0]
    return AUDIT_LEGACY if fields[0] == "-" else AUDIT_UNKNOWN


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
        elif len(parts) in (7, 8) and parts[0] == "RUN" and now is not None:
            # A dispatcher that predates the audit field sends 7 parts: its audit is unknown.
            _, repo, run_id, kind, stamp, e2e, smoke, *audit = parts
            if not (_REPO_RE.match(repo) and _RUN_RE.match(run_id) and kind in ("ready", "pending") and stamp.isdigit()):
                malformed.append("RUN line failed validation")
                continue
            runs.append(RunEntry(
                repo, run_id, kind == "ready", max(0, now - int(stamp)),
                e2e if _OUTCOME_RE.match(e2e) else "unknown",
                smoke if _OUTCOME_RE.match(smoke) else "unknown",
                _audit_word(audit),
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


def decode_audit(output: str, repo: str) -> bytes:
    """The run's ``audit.json`` bytes from the script's base64, or :class:`Refusal`.
    Adopter code wrote it, so it must fit the cap, parse as an object and name
    the repo whose folder it came from: the filing tool derives the target repo
    from that field, and a file in one adopter's folder must not file in another's."""
    try:
        data = base64.b64decode("".join(output.split()), validate=True)
    except (binascii.Error, ValueError):
        raise Refusal("a copied audit.json was not valid base64") from None
    if len(data) > MAX_AUDIT_BYTES:
        raise Refusal(f"audit.json is over the {MAX_AUDIT_BYTES // 1024} KiB cap")
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refusal("audit.json is not valid JSON") from None
    if not isinstance(parsed, dict) or parsed.get("repo") != repo:
        raise Refusal("audit.json does not name the repo of its run folder")
    return data


# ----------------------------------------------------------------- delivery


def make_send_file_sender(script: Path, env: Optional[Dict[str, str]] = None) -> Sender:
    """A :data:`Sender` that runs fleet-config's send-file script and reads its
    one result line (``SENT=`` / ``REFUSED=`` / ``UNAVAILABLE=``). ``env`` is
    the script's whole environment; None inherits this process's."""

    def send(path: Path, caption: str) -> SendOutcome:
        try:
            result = subprocess.run(
                [sys.executable, str(script), str(path), "--caption", caption],
                capture_output=True, text=True, timeout=_SEND_TIMEOUT_S, creationflags=NO_WINDOW, env=env,
            )
        except subprocess.TimeoutExpired:
            return SendOutcome("unavailable", f"send_file.py gave no answer in {_SEND_TIMEOUT_S}s")
        except OSError as exc:
            return SendOutcome("unavailable", f"send_file.py could not run ({type(exc).__name__})")
        return parse_send_result(result.stdout)

    return send


class JobSender:
    """The :data:`Sender` for a pass run by a launcher Job.

    send_file.py runs without the inherited session id, so a Job can only ever
    deliver through a route meant for Jobs, never into whichever session's chat
    that id happens to name. An image that is not sent is copied into the Job
    run's artifact folder (shown in the Jobs tab) and reported ``kept``: the
    run stays undelivered and the next pass retries it."""

    def __init__(self, script: Path, artifact_dir: Path, env: Dict[str, str]) -> None:
        self.artifact_dir = artifact_dir
        self.inherited_session = bool(env.get(SESSION_ENV, "").strip())
        self._send = make_send_file_sender(script, {k: v for k, v in env.items() if k != SESSION_ENV})
        self.kept = 0

    def __call__(self, path: Path, caption: str) -> SendOutcome:
        outcome = self._send(path, caption)
        if outcome.kind == "sent":
            return outcome
        # The artifact route lists direct children only, so the run's staging folder name prefixes the file.
        target = self.artifact_dir / f"{path.parent.name}-{path.name}"
        try:
            make_dirs(self.artifact_dir)
            copy_file(path, target)
        except OSError as exc:
            return SendOutcome(outcome.kind, f"{outcome.detail}; copying to the Job's artifacts failed ({type(exc).__name__})")
        self.kept += 1
        return SendOutcome("kept", f"{outcome.detail}; copied to the Job's artifacts")

    def pass_note(self) -> Optional[str]:
        """The one pass-level line saying why images were kept, or None when none were."""
        if not self.kept:
            return None
        ignored = "inherited session id ignored" if self.inherited_session else "no session id"
        return (f"⚠️ not_confirmed: a launcher Job has no launcher session ({ignored}); "
                f"{self.kept} image(s) copied to this Job run's artifacts instead of the chat")


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


def make_routing_audit_filer(script: Path) -> Filer:
    """A :data:`Filer` that runs project-scaffolding's ``routing_audit.py file``
    and reads its ``FILE=`` line. No line, or a failure to run, is ``unknown``."""

    def file_it(audit_path: Path) -> FileOutcome:
        try:
            result = subprocess.run(
                [sys.executable, str(script), "file", "--audit", str(audit_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                stdin=subprocess.DEVNULL, timeout=_FILE_TIMEOUT_S, creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return FileOutcome("unknown", f"routing_audit.py gave no answer in {_FILE_TIMEOUT_S}s")
        except OSError as exc:
            return FileOutcome("unknown", f"routing_audit.py could not run ({type(exc).__name__})")
        return parse_file_result(result.stdout)

    return file_it


def parse_file_result(stdout: str) -> FileOutcome:
    for line in reversed([ln.strip() for ln in stdout.splitlines() if ln.strip()]):
        found = _FILE_LINE_RE.match(line)
        if found:
            return FileOutcome(found.group(1), re.sub(r"[^\x20-\x7e]", "", found.group(2)).strip()[:120])
    return FileOutcome("unknown", "routing_audit.py printed no FILE= line")


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
    make_dirs(staging)
    try:
        return _copy_and_send(entry, images, ssh, send, conn, staging)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _copy_and_send(entry: RunEntry, images: List[RemoteImage], ssh: Ssh, send: Sender, conn, staging: Path) -> RunOutcome:
    repo, run_id = entry.repo, entry.run_id
    sent_now = kept = 0
    last_kept = ""
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
        save_bytes(path, data)
        outcome = send(path, caption_for(entry, image.rel, index, len(images)))
        if outcome.kind == "kept":
            kept += 1
            last_kept = outcome.detail
            continue
        if outcome.kind != "sent":
            return RunOutcome(repo, run_id, NOT_CONFIRMED, f"send {outcome.kind}: {outcome.detail}")
        store.mark_file_sent(conn, repo, run_id, image.rel)
        sent_now += 1
    if kept:
        return RunOutcome(repo, run_id, NOT_CONFIRMED, f"{kept} not delivered, {sent_now} sent now: {last_kept}")
    store.mark_run_delivered(conn, repo, run_id, len(images))
    return RunOutcome(repo, run_id, DELIVERED, f"{sent_now} sent now, {len(images)} in the run")


def file_audit(entry: RunEntry, ssh: Ssh, filer: Optional[Filer], conn, staging_root: Path) -> RunOutcome:
    """Copy a ``miss`` run's audit.json and have the filing tool file or update the
    adopter's one issue. Recorded only when the tool confirmed an outcome."""
    repo, run_id = entry.repo, entry.run_id
    if filer is None:
        return RunOutcome(repo, run_id, AUDIT_NOT_MEASURED, "routing_audit.py not found; pass --routing-audit")
    got = ssh(f"audit {repo} {run_id}", _SSH_GET_TIMEOUT_S)
    if not got.ok:
        return RunOutcome(repo, run_id, AUDIT_NOT_MEASURED, f"copying audit.json failed: {_ssh_failure(got)}")
    try:
        data = decode_audit(got.stdout, repo)
    except Refusal as exc:
        return RunOutcome(repo, run_id, REFUSED, str(exc))
    staging = staging_root / f"{repo}-{run_id}-audit"
    shutil.rmtree(staging, ignore_errors=True)
    make_dirs(staging)
    try:
        path = staging / "audit.json"
        save_bytes(path, data)
        filed = filer(path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if filed.state in ("created", "commented", "unchanged"):
        store.mark_audit_filed(conn, repo, run_id, filed.state)
        return RunOutcome(repo, run_id, AUDIT_FILED, f"FILE={filed.state} {filed.detail}".strip())
    # none: the file contradicts READY's audit=miss, so neither is trusted; unknown: gh could not confirm.
    return RunOutcome(repo, run_id, AUDIT_NOT_MEASURED, f"FILE={filed.state} {filed.detail}".strip())


def run_pass(ssh: Ssh, send: Sender, conn, staging_root: Path, *, window_days: int = DEFAULT_WINDOW_DAYS,
             dry_run: bool = False, filer: Optional[Filer] = None,
             gh: Optional[nightly_ci_issues.Gh] = None, now: Optional[float] = None) -> PassReport:
    """One pull-and-deliver pass over the Mac's results folder. With ``gh`` it also files
    each new night's result (``nightly_ci``): nothing is filed or sent for a night without it."""
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
            continue
        if not entry.ready:
            if entry.age_s > STALE_PENDING_S:
                report.outcomes.append(RunOutcome(entry.repo, entry.run_id, LEGACY, "no READY marker a day after the last write; it will not become measurable"))
            else:
                report.outcomes.append(RunOutcome(entry.repo, entry.run_id, PENDING, "no READY yet"))
            continue
        need_images = not store.run_delivered(conn, entry.repo, entry.run_id)
        need_audit = entry.audit == AUDIT_MISS and not store.audit_filed(conn, entry.repo, entry.run_id)
        if entry.audit == AUDIT_UNKNOWN:
            # Never folded into clean: the night was not measured. Not recorded, so every pass says so.
            report.outcomes.append(RunOutcome(entry.repo, entry.run_id, AUDIT_NOT_MEASURED, "READY records no measured routing audit"))
        elif entry.audit == AUDIT_LEGACY:
            report.outcomes.append(RunOutcome(entry.repo, entry.run_id, LEGACY, "READY predates the routing audit; not measurable, not counted"))
        if not (need_images or need_audit):
            report.already_delivered += 1
        elif dry_run:
            if need_images:
                report.outcomes.append(RunOutcome(entry.repo, entry.run_id, WOULD_DELIVER, "READY, not yet delivered"))
            if need_audit:
                report.outcomes.append(RunOutcome(entry.repo, entry.run_id, WOULD_FILE, "audit=miss, not yet filed"))
        elif attempted >= MAX_RUNS_PER_PASS:
            report.outcomes.append(RunOutcome(entry.repo, entry.run_id, PENDING, "left for the next pass (per-pass cap)"))
        else:
            attempted += 1
            if need_images:
                report.outcomes.append(deliver_run(entry, ssh, send, conn, staging_root))
            if need_audit:
                report.outcomes.append(file_audit(entry, ssh, filer, conn, staging_root))
    if gh is not None:
        # A pre-audit folder is already classified legacy above; a second class (ci_unknown) would
        # count it as bad though it can never become measurable.
        nights = [(e.repo, e.run_id, e.age_s) for e in runs if e.ready and e.age_s <= window_s and e.audit != AUDIT_LEGACY]
        report.ci_outcomes = nightly_ci.run_ci_stage(
            nights, ssh, gh, send, conn, staging_root, now=time.time() if now is None else now, dry_run=dry_run)
    return report


# ---------------------------------------------------------------------- CLI


def _default_send_file() -> Path:
    # fleet-config is a sibling checkout of this repo (or of its -wt worktree).
    return Path(__file__).resolve().parents[3] / "fleet-config" / "skills" / "_lib" / "send_file.py"


def _default_routing_audit() -> Path:
    # project-scaffolding is a sibling checkout, like fleet-config.
    return Path(__file__).resolve().parents[3] / "project-scaffolding" / "scripts" / "routing_audit.py"


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
    if not report.outcomes:
        print("ℹ️ nothing new to deliver")
    icons = {DELIVERED: "✅", EMPTY: "ℹ️", PENDING: "ℹ️", WOULD_DELIVER: "ℹ️", REFUSED: "❌", NOT_CONFIRMED: "⚠️",
             AUDIT_FILED: "✅", AUDIT_NOT_MEASURED: "⚠️", WOULD_FILE: "ℹ️", LEGACY: "ℹ️"}
    for o in report.outcomes:
        print(f"{icons[o.state]} {o.repo} run {o.run_id}: {o.state} - {o.detail}")
    ci_icons = {nightly_ci.REPORTED: "✅", nightly_ci.WOULD_REPORT: "ℹ️", nightly_ci.DIGEST_SENT: "✅",
                nightly_ci.UNKNOWN: "⚠️", nightly_ci.REFUSED: "❌", nightly_ci.DIGEST_NOT_CONFIRMED: "⚠️"}
    for c in report.ci_outcomes:
        print(f"{ci_icons[c.state]} {c.repo} run {c.run_id}: {c.state} - {c.detail}")
    print(f"ℹ️ already delivered: {report.already_delivered}; outside the pull window: {report.out_of_window}")
    print(_verdict_line(report))


def _verdict_line(report: PassReport) -> str:
    """One line that says why the pass is green or red. A missing setup never reaches
    it: that exits 2 with its own ``setup missing`` line before the Mac is read."""
    if report.attention:
        return f"⚠️ verdict: red - {report.attention} item(s) unknown, refused or not confirmed (listed above)"
    if report.legacy:
        return f"ℹ️ verdict: ok - old folders only: {report.legacy} legacy, not measurable and not counted; setup is complete"
    return "ℹ️ verdict: ok"


def _write_status(code: int) -> None:
    """Record the exit code where a wrapping job can read it: a headless agent
    session exits 0 whatever its pass reported, so the job reads this instead."""
    target = os.environ.get(STATUS_ENV, "").strip()
    if not target:
        return
    try:
        make_dirs(Path(target).parent)
        save_text(target, f"{code}\n", encoding="ascii")
    except OSError as exc:
        # No file means the job reports "not confirmed", never a pass.
        print(f"⚠️ could not write the status file ({exc.__class__.__name__})")


def main(argv: Optional[List[str]] = None) -> int:
    code = _main(argv)
    _write_status(code)
    return code


def _main(argv: Optional[List[str]]) -> int:
    parser = argparse.ArgumentParser(description="Pull the nightly Mac screenshots and deliver them to the owner's chat.")
    parser.add_argument("--host", default=None, help=f"machine id of the Mac runner host (config/machines.local.yaml); default: ${HOST_ENV} from .env")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--dry-run", action="store_true", help="list only: no copy, no send, nothing recorded")
    parser.add_argument("--send-file", default=None, help="path to send_file.py (default: the fleet-config checkout beside this repo)")
    parser.add_argument("--routing-audit", default=None, help="path to routing_audit.py (default: the project-scaffolding checkout beside this repo)")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    except ImportError:
        pass
    host_id = (args.host or os.environ.get(HOST_ENV, "")).strip()
    if not host_id:
        print(f"❌ setup missing - no host: pass --host or set {HOST_ENV} in .env")
        return 2
    ssh, problem = _ssh_for(host_id)
    if ssh is None:
        print(f"❌ setup missing - {problem}")
        return 2
    send_file = Path(args.send_file) if args.send_file else _default_send_file()
    if not send_file.is_file():
        print("❌ setup missing - send_file.py not found; pass --send-file")
        return 2
    # A missing filing script does not stop the screenshots: a miss run then reports audit_unknown.
    routing_audit = Path(args.routing_audit) if args.routing_audit else _default_routing_audit()
    filer = make_routing_audit_filer(routing_audit) if routing_audit.is_file() else None
    artifact_dir = os.environ.get(JOB_ARTIFACT_ENV, "").strip()
    job_sender: Optional[JobSender] = None
    if artifact_dir:
        job_sender = JobSender(send_file, Path(artifact_dir), dict(os.environ))
    sender: Sender = job_sender if job_sender is not None else make_send_file_sender(send_file)
    conn = store.connect()
    try:
        report = run_pass(ssh, sender, conn, _staging_root(), window_days=args.window_days,
                          dry_run=args.dry_run, filer=filer, gh=nightly_ci_issues.make_gh())
    finally:
        conn.close()
    _print_report(report)
    note = job_sender.pass_note() if job_sender is not None else None
    if note:
        print(note)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
