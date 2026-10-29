"""Why a peer hub is out of sync with this one, and which action fixes it.

Comparing the two served SHAs says *that* they differ, never *which side is
behind*. The Sync verb only moves a peer to ``origin/main``, so it closes the
gap in exactly one case; this module resolves both served builds against this
hub's own checkout and ``origin/main`` and names the case, so the Machines tab
can offer the fix that works instead of a Sync that silently changes nothing.

``classify`` is pure: it takes a ``compare`` callable so the case table is
unit-testable without git. :func:`diagnose` binds it to this repo's checkout.
A SHA that cannot be resolved is its own ``unknown`` verdict, never folded
into "in sync" or into a guessed cause.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from ..build_info import PROJECT_ROOT, resolve_git_sha
from ..no_window import NO_WINDOW

logger = logging.getLogger(__name__)

IN_SYNC = "in_sync"
UNKNOWN = "unknown"
RESTART_LOCAL = "restart_local"  # this hub runs an older build than its checkout
PULL_LOCAL = "pull_local"  # this hub's checkout is behind origin/main
PEER_BEHIND = "peer_behind"  # the one case Sync fixes
PEER_AHEAD = "peer_ahead"  # the peer runs commits past origin/main
UNMERGED_LOCAL = "unmerged_local"  # this hub runs work that is not on main
UNMERGED_PEER = "unmerged_peer"  # the peer runs work that is not on main

SAME, BEHIND, AHEAD, DIVERGED = "same", "behind", "ahead", "diverged"


@dataclass(frozen=True)
class Relation:
    """How commit ``a`` stands against commit ``b`` (``count`` = commits apart)."""

    kind: str  # SAME | BEHIND (a is an ancestor of b) | AHEAD | DIVERGED
    count: int = 0


Compare = Callable[[str, str], Optional[Relation]]


def _usable(sha: Optional[str]) -> bool:
    return bool(sha) and sha != "unknown"


def _commits(n: int) -> str:
    return f"{n} commit" + ("" if n == 1 else "s")


def _verdict(kind: str, reason: str, *, sync_helps: bool = False) -> Dict[str, object]:
    return {"kind": kind, "reason": reason, "sync_helps": sync_helps}


def classify(
    *,
    peer_name: str,
    git_sha_match: Optional[bool],
    local_running: Optional[str],
    peer_running: Optional[str],
    local_checkout: Optional[str],
    origin_main: Optional[str],
    fetch_ok: bool,
    compare: Compare,
) -> Dict[str, object]:
    """Name the case behind a served-SHA comparison.

    ``origin_main`` is only trusted when ``fetch_ok``: a stale tracking ref
    would make "the peer is behind main" a guess, so it reads as ``unknown``.
    Order matters: this hub's own staleness is settled first because no peer
    action can close a gap that opens on this side.
    """
    if git_sha_match is True:
        return _verdict(IN_SYNC, "Both hubs run the same build.")
    if git_sha_match is None or not (_usable(local_running) and _usable(peer_running)):
        return _verdict(UNKNOWN, "A build id could not be read, so the two hubs were not compared.")
    if not _usable(local_checkout):
        return _verdict(UNKNOWN, "This hub's checkout could not be read, so the cause is not confirmed.")

    ran = compare(local_running, local_checkout)
    if ran is None:
        return _verdict(UNKNOWN, "This hub's running build was not found in its checkout, so the cause is not confirmed.")
    if ran.kind != SAME:
        gap = f" by {_commits(ran.count)}" if ran.kind == BEHIND and ran.count else ""
        return _verdict(
            RESTART_LOCAL,
            f"This hub is running an older build than its own checkout{gap}. Restart this hub.",
        )

    if not (_usable(origin_main) and fetch_ok):
        return _verdict(UNKNOWN, "main could not be checked, so whether Sync would help is not confirmed.")

    local_vs_main = compare(local_running, origin_main)
    if local_vs_main is None:
        return _verdict(UNMERGED_LOCAL, "This hub runs a build that is not on main. Neither Sync nor a restart fixes that.")
    if local_vs_main.kind == BEHIND:
        return _verdict(
            PULL_LOCAL,
            f"This hub is {_commits(local_vs_main.count)} behind main. Pull and restart this hub.",
        )
    if local_vs_main.kind in (AHEAD, DIVERGED):
        return _verdict(UNMERGED_LOCAL, "This hub runs work that is not on main. Neither Sync nor a restart fixes that.")

    peer_vs_main = compare(peer_running, origin_main)
    if peer_vs_main is None:
        return _verdict(UNMERGED_PEER, f"{peer_name} runs a build that is not on main. Sync would not change that.")
    if peer_vs_main.kind == SAME:
        # Differently abbreviated ids for one commit; the exact compare disagrees with the string one.
        return _verdict(IN_SYNC, "Both hubs run the same build.")
    if peer_vs_main.kind == BEHIND:
        return _verdict(
            PEER_BEHIND,
            f"{peer_name} is {_commits(peer_vs_main.count)} behind main. Sync updates it.",
            sync_helps=True,
        )
    if peer_vs_main.kind == AHEAD:
        return _verdict(
            PEER_AHEAD,
            f"{peer_name} is {_commits(peer_vs_main.count)} ahead of this hub. Sync only moves it to main, so it would not close the gap.",
        )
    return _verdict(UNMERGED_PEER, f"{peer_name} runs work that is not on main. Sync would not change that.")


# --------------------------------------------------------- git bindings
_GIT_TIMEOUT_S = 5.0
_FETCH_TIMEOUT_S = 20.0
# One fetch per window however many peers or polls ask: the Machines tab polls
# every few seconds and each out-of-sync peer would otherwise fetch again.
_FETCH_TTL_S = 300.0
_FETCH_RETRY_S = 60.0

_fetch_lock = threading.Lock()
_fetch_state: Dict[str, float] = {"at": 0.0, "ok": 0.0}


def _git(root: Path, *args: str, timeout: float = _GIT_TIMEOUT_S) -> Optional["subprocess.CompletedProcess[str]"]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, stdin=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("⚠️ git %s failed: %s", args[0], exc)
        return None


def refresh_origin_main(root: Path = PROJECT_ROOT) -> bool:
    """Fetch ``origin/main`` at most once per window; whether it is current.

    A failed fetch is retried sooner than a good one is refreshed, and reports
    ``False`` meanwhile: the tracking ref may be stale, which the classifier
    must not read as "main".
    """
    with _fetch_lock:
        now = time.monotonic()
        ttl = _FETCH_TTL_S if _fetch_state["ok"] else _FETCH_RETRY_S
        if _fetch_state["at"] and now - _fetch_state["at"] < ttl:
            return bool(_fetch_state["ok"])
        res = _git(root, "fetch", "--quiet", "origin", "main", timeout=_FETCH_TIMEOUT_S)
        ok = res is not None and res.returncode == 0
        if not ok:
            logger.info("⚠️ git fetch origin main failed: %s", (res.stderr.strip() if res else "no git"))
        _fetch_state["at"], _fetch_state["ok"] = now, 1.0 if ok else 0.0
        return ok


def _full_sha(root: Path, sha: str) -> Optional[str]:
    res = _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    out = res.stdout.strip() if res is not None and res.returncode == 0 else ""
    return out or None


def _is_ancestor(root: Path, a: str, b: str) -> Optional[bool]:
    res = _git(root, "merge-base", "--is-ancestor", a, b)
    if res is None or res.returncode not in (0, 1):
        return None
    return res.returncode == 0


def _count(root: Path, a: str, b: str) -> int:
    res = _git(root, "rev-list", "--count", f"{a}..{b}")
    try:
        return int(res.stdout.strip()) if res is not None and res.returncode == 0 else 0
    except ValueError:
        return 0


def make_compare(root: Path = PROJECT_ROOT) -> Compare:
    """A ``compare`` bound to ``root``'s object store; ``None`` for a commit it lacks."""

    def compare(a: str, b: str) -> Optional[Relation]:
        fa, fb = _full_sha(root, a), _full_sha(root, b)
        if fa is None or fb is None:
            return None
        if fa == fb:
            return Relation(SAME)
        a_in_b, b_in_a = _is_ancestor(root, fa, fb), _is_ancestor(root, fb, fa)
        if a_in_b is None or b_in_a is None:
            return None
        if a_in_b:
            return Relation(BEHIND, _count(root, fa, fb))
        if b_in_a:
            return Relation(AHEAD, _count(root, fb, fa))
        return Relation(DIVERGED)

    return compare


def diagnose(
    *,
    peer_name: str,
    git_sha_match: Optional[bool],
    local_running: Optional[str],
    peer_running: Optional[str],
    root: Path = PROJECT_ROOT,
) -> Dict[str, object]:
    """:func:`classify` against this hub's checkout and ``origin/main``.

    Blocking (git subprocesses): call from a worker thread. A matching or
    uncompared pair needs no git at all.
    """
    if git_sha_match is not False:
        return classify(
            peer_name=peer_name, git_sha_match=git_sha_match,
            local_running=local_running, peer_running=peer_running,
            local_checkout=None, origin_main=None, fetch_ok=False,
            compare=lambda a, b: None,
        )
    checkout = resolve_git_sha(root)
    fetch_ok = refresh_origin_main(root)
    main_sha = _full_sha(root, "origin/main") if fetch_ok else None
    verdict = classify(
        peer_name=peer_name, git_sha_match=git_sha_match,
        local_running=local_running, peer_running=peer_running,
        local_checkout=checkout, origin_main=main_sha, fetch_ok=fetch_ok,
        compare=make_compare(root),
    )
    logger.info(
        "🔃 out-of-sync %s: %s (this=%s checkout=%s peer=%s main=%s)",
        peer_name, verdict["kind"], local_running, checkout, peer_running,
        (main_sha or "?")[:7],
    )
    return verdict
