"""Turn the nightly Mac gate's per-run result into GitHub issues, a flake ledger and a digest.

The Mac workflow marks a finished run folder ``READY`` with the commit tested,
the night's ``result`` and one result per leg. Once hosted CI is retired this
host is the only place that can act on a red ``main``: it files or updates one
``ci-red-main`` issue per repo, closes it on the next green night, counts each
night's flaky tests in a host-local ledger (a test flaky on three nights within
fourteen gets one ``flaky-test`` issue) and sends the owner one digest line.

The run folder is data produced by adopter code. ``READY`` is read through the
Mac's ``ready`` verb and re-validated field by field here; the junit files and
flaky-id lists come through its allowlisted ``text`` verb and are only parsed
(XML with a DOCTYPE or entity is refused before a parser sees it), never run.
Only a SHA, leg names, counts and test node ids reach GitHub.

Unknown stays unknown: a ``READY`` without a known result, a file that could not
be read, a ``gh`` call that could not be confirmed is its own state, the night is
not recorded as reported, and the next pass retries it.
"""

from __future__ import annotations

import base64
import binascii
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from ..fsio.runtime_files import make_dirs, save_text
from . import mac_results_store as store
from . import nightly_ci_issues as issues
from .ssh_exec import SshResult


class SendResult(Protocol):
    """What the pass's sender answers: ``kind == "sent"`` is the only confirmed delivery."""

    kind: str
    detail: str


Ssh = Callable[[str, int], SshResult]
Sender = Callable[[Path, str], SendResult]

FLAKY_NIGHTS = 3
FLAKY_WINDOW_DAYS = 14
# Ledger rows and digested nights older than this are dropped; well past the 14-day rule.
LEDGER_KEEP_DAYS = 30
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_IDS_SHOWN = 50
MAX_ID_LEN = 200

_SSH_TIMEOUT_S = 90
_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
_WORD_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# No slash or backslash: a path from the runner has no business in an issue.
_ID_RE = re.compile(r"^[A-Za-z0-9_.:\[\]\-=+,() ]{1,%d}$" % MAX_ID_LEN)
_LEG_RESULTS = ("pass", "flaky", "fail", "not-run", "unknown")
LEGS = ("unit", "e2e", "smoke")
# The junit files of a leg: the retry's (which holds what still failed) first, then the first attempt's.
_JUNIT = {"unit": ("unit-retry.xml", "unit.xml"), "e2e": ("junit-retry.xml", "junit.xml")}
_FLAKY_FILE = {"unit": "unit-flaky.txt", "e2e": "e2e-flaky.txt"}

# Outcome states, one per night per pass.
REPORTED = "ci_reported"
UNKNOWN = "ci_unknown"
REFUSED = "ci_refused"
WOULD_REPORT = "ci_would_report"
DIGEST_SENT = "digest_sent"
DIGEST_NOT_CONFIRMED = "digest_not_confirmed"


@dataclass(frozen=True)
class CiOutcome:
    repo: str
    run_id: str
    state: str
    detail: str = ""


@dataclass(frozen=True)
class Night:
    repo: str
    run_id: str
    sha: str
    result: str  # green | red | unknown
    legs: Dict[str, str]
    flaky: int


class NightRefusal(Exception):
    """READY could not be trusted; the message is the reason shown."""


def parse_ready(text: str, repo: str, run_id: str) -> Night:
    """The night from a ``ready`` verb's output. Every value is checked against its
    own vocabulary; one outside it reads as ``unknown``, never as a pass."""
    fields: Dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key not in fields:
            fields[key] = value
    if fields.get("run", run_id) != run_id or fields.get("repo", repo) != repo:
        raise NightRefusal("READY does not name the run folder it came from")
    legs = {}
    for leg in LEGS:
        value = fields.get(leg, "unknown").replace("_", "-")
        legs[leg] = value if value in _LEG_RESULTS else "unknown"
    result = fields.get("result", "unknown")
    if result not in ("green", "red") or (result == "green" and "fail" in legs.values()):
        # A green night with a failed leg contradicts itself: neither is trusted.
        result = "unknown"
    sha = fields.get("sha", "unknown")
    flaky = fields.get("flaky", "")
    return Night(repo, run_id, sha if _SHA_RE.match(sha) else "unknown", result, legs,
                 int(flaky) if flaky.isdigit() and len(flaky) <= 4 else 0)


def clean_ids(candidates: Sequence[str]) -> Tuple[List[str], int]:
    """``(ids to show, how many were left out)``: sorted, deduped, only ids that fit the pattern, capped."""
    unique = sorted({c.strip() for c in candidates if c.strip()})
    fit = [c for c in unique if _ID_RE.match(c)]
    return fit[:MAX_IDS_SHOWN], len(unique) - len(fit[:MAX_IDS_SHOWN])


def failed_ids(xml_bytes: bytes) -> Optional[List[str]]:
    """``classname::name`` of each failed or errored testcase; None when the XML is refused or unreadable."""
    lowered = xml_bytes.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        return None
    try:
        root = ET.fromstring(xml_bytes)  # noqa: S314 - DOCTYPE and entities refused above
    except ET.ParseError:
        return None
    return [f"{c.get('classname', '')}::{c.get('name', '')}" for c in root.iter("testcase")
            if c.find("failure") is not None or c.find("error") is not None]


# ----------------------------------------------------------------- evidence

_ABSENT = "absent"  # the Mac answered: there is no such file in the run
_NOT_CONFIRMED = "unknown"  # the Mac could not be asked or answered something unusable


def _decode(output: str) -> Optional[bytes]:
    try:
        data = base64.b64decode("".join(output.split()), validate=True)
    except (binascii.Error, ValueError):
        return None
    return data if len(data) <= MAX_TEXT_BYTES else None


def fetch_evidence(ssh: Ssh, repo: str, run_id: str, name: str) -> Tuple[str, bytes]:
    """``("ok", bytes)``, ``("absent", b"")`` or ``("unknown", b"")``."""
    got = ssh(f"text {repo} {run_id} {name}", _SSH_TIMEOUT_S)
    if got.error is None and got.returncode == 3:
        return _ABSENT, b""
    data = _decode(got.stdout) if got.ok else None
    return ("ok", data) if data is not None else (_NOT_CONFIRMED, b"")


def failing_ids_for(ssh: Ssh, repo: str, run_id: str, night: Night) -> Tuple[List[str], Optional[str]]:
    """Ids still failing in each failed junit leg. The problem is set only when the Mac
    could not be asked; a file that is simply not there leaves that leg's ids unnamed."""
    found: List[str] = []
    for leg, names in _JUNIT.items():
        if night.legs.get(leg) != "fail":
            continue
        for name in names:
            state, data = fetch_evidence(ssh, repo, run_id, name)
            if state == _NOT_CONFIRMED:
                return [], f"could not read {name}"
            if state == "ok":
                ids = failed_ids(data)
                if ids is not None:
                    found += ids
                    break
    return found, None


def flaky_ids_for(ssh: Ssh, repo: str, run_id: str, night: Night) -> Tuple[List[str], Optional[str]]:
    found: List[str] = []
    for leg, name in _FLAKY_FILE.items():
        if night.legs.get(leg) != "flaky":
            continue
        state, data = fetch_evidence(ssh, repo, run_id, name)
        if state == _NOT_CONFIRMED:
            return [], f"could not read {name}"
        if state == "ok":
            found += data.decode("utf-8", errors="replace").splitlines()
    return found, None


# ------------------------------------------------------------------ a night


@dataclass
class CiContext:
    ssh: Ssh
    gh: issues.Gh
    conn: object
    now: float


def _issue_step(ctx: CiContext, night: Night, night_at: float) -> Tuple[str, str]:
    """The ci-red-main action for the night, as ``(state word, detail)``."""
    if store.newest_reported_night(ctx.conn, night.repo) > night_at:
        return "superseded", "a newer night was already reported"
    if night.result == "green":
        return issues.close_green(ctx.gh, night.repo, night.sha)
    ids, problem = failing_ids_for(ctx.ssh, night.repo, night.run_id, night)
    if problem:
        return issues.UNKNOWN, problem
    shown, withheld = clean_ids(ids)
    return issues.file_red(ctx.gh, night.repo, night.sha, night.legs, shown, withheld)


def _flaky_step(ctx: CiContext, night: Night, night_at: float) -> Tuple[List[str], Optional[str]]:
    """Record the night's flaky ids in the ledger and file any that reached the rule.
    Returns ``(filing details, problem)``."""
    ids, problem = flaky_ids_for(ctx.ssh, night.repo, night.run_id, night)
    if problem:
        return [], problem
    shown, _ = clean_ids(ids)
    store.record_flaky(ctx.conn, night.repo, night.run_id, shown, night_at)
    since = ctx.now - FLAKY_WINDOW_DAYS * 86400
    notes: List[str] = []
    for test_id in shown:
        nights = store.flaky_night_count(ctx.conn, night.repo, test_id, since)
        if nights < FLAKY_NIGHTS:
            continue
        state, detail = issues.file_flaky(ctx.gh, night.repo, test_id, nights, FLAKY_WINDOW_DAYS)
        if state == issues.UNKNOWN:
            return notes, f"flaky filing not confirmed ({detail})"
        if state != issues.UNCHANGED:
            notes.append(f"flaky-test {state} {detail}")
    return notes, None


def report_night(ctx: CiContext, repo: str, run_id: str, age_s: int) -> CiOutcome:
    got = ctx.ssh(f"ready {repo} {run_id}", _SSH_TIMEOUT_S)
    if not got.ok:
        return CiOutcome(repo, run_id, UNKNOWN, "READY could not be read from the Mac")
    try:
        night = parse_ready(got.stdout, repo, run_id)
    except NightRefusal as exc:
        return CiOutcome(repo, run_id, REFUSED, str(exc))
    if night.result == "unknown":
        return CiOutcome(repo, run_id, UNKNOWN, "READY records no known result for the night")
    night_at = ctx.now - age_s
    state, detail = _issue_step(ctx, night, night_at)
    if state == issues.UNKNOWN:
        return CiOutcome(repo, run_id, UNKNOWN, detail)
    notes, problem = _flaky_step(ctx, night, night_at)
    if problem:
        return CiOutcome(repo, run_id, UNKNOWN, problem)
    store.mark_ci_reported(ctx.conn, repo, run_id, night.result, night.flaky, night_at)
    summary = f"{night.result}: ci-red-main {state} {detail}".strip()
    return CiOutcome(repo, run_id, REPORTED, "; ".join([summary, *notes]))


# ------------------------------------------------------------------- digest


def digest_line(nights: Sequence[tuple], unknown_repos: Sequence[str]) -> str:
    """``Nightly CI: 6 run, 5 green, 1 red (task-os), 2 flaky``; unknown nights are named, never counted green."""
    red = sorted({n[0] for n in nights if n[2] == "red"})
    parts = [f"{len(nights) + len(unknown_repos)} run", f"{sum(1 for n in nights if n[2] == 'green')} green"]
    if red:
        parts.append(f"{len(red)} red ({', '.join(red)})")
    if unknown_repos:
        parts.append(f"{len(unknown_repos)} unknown ({', '.join(sorted(set(unknown_repos)))})")
    flaky = sum(n[3] for n in nights)
    if flaky:
        parts.append(f"{flaky} flaky")
    return "Nightly CI: " + ", ".join(parts)


def send_digest(conn, send: Sender, staging_root: Path, unknown_repos: Sequence[str]) -> Optional[CiOutcome]:
    """Send one line for every reported night not yet digested; None when there is none."""
    nights = store.undigested_nights(conn)
    if not nights:
        return None
    line = digest_line(nights, unknown_repos)
    folder = staging_root / "nightly-digest"
    make_dirs(folder)
    path = folder / "digest.txt"
    save_text(path, line + "\n")
    try:
        outcome = send(path, line)
    finally:
        path.unlink(missing_ok=True)
    if outcome.kind == "sent":
        store.mark_digested(conn, nights)
        return CiOutcome("-", "-", DIGEST_SENT, line)
    return CiOutcome("-", "-", DIGEST_NOT_CONFIRMED, f"{line} ({outcome.detail})")


# -------------------------------------------------------------------- stage


def run_ci_stage(entries: Sequence[Tuple[str, str, int]], ssh: Ssh, gh: issues.Gh, send: Sender, conn,
                 staging_root: Path, *, now: float, dry_run: bool = False) -> List[CiOutcome]:
    """Report every ``(repo, run id, age in s)`` night not yet reported, oldest first (so a
    later green closes an earlier red and never the reverse), then send the digest."""
    pending = [e for e in entries if not store.ci_reported(conn, e[0], e[1])]
    if dry_run:
        return [CiOutcome(r, i, WOULD_REPORT, "READY, not yet reported") for r, i, _ in pending]
    ctx = CiContext(ssh, gh, conn, now)
    outcomes = [report_night(ctx, repo, run_id, age) for repo, run_id, age in sorted(pending, key=lambda e: -e[2])]
    unknown = [o.repo for o in outcomes if o.state == UNKNOWN]
    digest = send_digest(conn, send, staging_root, unknown)
    if digest is not None:
        outcomes.append(digest)
    store.prune_ci(conn, now - LEDGER_KEEP_DAYS * 86400)
    return outcomes
