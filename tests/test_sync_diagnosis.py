"""Why a peer hub reads "out of sync", and whether Sync can close it.

The classifier is pure over a ``compare`` callable, so every case in the table
is pinned against a tiny fake commit graph; the git binding is exercised once
against a throwaway repository.
"""

from __future__ import annotations

import subprocess

import pytest

from src.fleet import sync_diagnosis as sd
from src.no_window import NO_WINDOW

# Linear history a <- b <- c <- d (d newest); "x" forks off b.
_LINE = ["a", "b", "c", "d"]
_PARENT = {"a": None, "b": "a", "c": "b", "d": "c", "x": "b"}


def _ancestors(sha):
    out = set()
    while sha:
        out.add(sha)
        sha = _PARENT[sha]
    return out


def _compare(a, b):
    if a not in _PARENT or b not in _PARENT:
        return None
    if a == b:
        return sd.Relation(sd.SAME)
    a_up, b_up = _ancestors(a), _ancestors(b)
    if a in b_up:
        return sd.Relation(sd.BEHIND, len(b_up - a_up))
    if b in a_up:
        return sd.Relation(sd.AHEAD, len(a_up - b_up))
    return sd.Relation(sd.DIVERGED)


_DERIVE = object()


def _classify(local="d", peer="d", checkout="d", main="d", match=_DERIVE, fetch_ok=True):
    if match is _DERIVE:
        match = local == peer
    return sd.classify(
        peer_name="Mac Mini", git_sha_match=match,
        local_running=local, peer_running=peer, local_checkout=checkout,
        origin_main=main, fetch_ok=fetch_ok, compare=_compare,
    )


def test_matching_builds_are_in_sync():
    assert _classify()["kind"] == sd.IN_SYNC


def test_uncompared_pair_is_unknown_not_in_sync():
    v = _classify(match=None)
    assert v["kind"] == sd.UNKNOWN and not v["sync_helps"]


def test_unreadable_build_id_is_unknown():
    assert _classify(peer="unknown", match=False)["kind"] == sd.UNKNOWN


def test_case_b_this_hub_runs_older_than_its_checkout():
    """The reported case: checkout fast-forwarded, process never restarted, peer already on main."""
    v = _classify(local="b", checkout="d", peer="d", main="d")
    assert v["kind"] == sd.RESTART_LOCAL
    assert "This hub is running an older build than its own checkout by 2 commits" in v["reason"]
    assert "Restart this hub" in v["reason"]
    assert v["sync_helps"] is False


def test_case_c_this_checkout_is_behind_main():
    v = _classify(local="b", checkout="b", peer="c", main="d")
    assert v["kind"] == sd.PULL_LOCAL
    assert "2 commits behind main" in v["reason"]
    assert v["sync_helps"] is False


def test_case_a_peer_is_behind_main_and_sync_helps():
    v = _classify(local="d", peer="b", checkout="d", main="d")
    assert v["kind"] == sd.PEER_BEHIND
    assert v["reason"] == "Mac Mini is 2 commits behind main. Sync updates it."
    assert v["sync_helps"] is True


def test_singular_commit_wording():
    assert "1 commit behind main" in _classify(local="d", peer="c", checkout="d", main="d")["reason"]


def test_case_e_peer_runs_past_main():
    v = _classify(local="c", peer="d", checkout="c", main="c")
    assert v["kind"] == sd.PEER_AHEAD
    assert "1 commit ahead of this hub" in v["reason"]
    assert v["sync_helps"] is False


def test_case_d_local_work_not_on_main():
    v = _classify(local="x", peer="c", checkout="x", main="c")
    assert v["kind"] == sd.UNMERGED_LOCAL and not v["sync_helps"]


def test_case_d_local_ahead_of_main():
    v = _classify(local="d", peer="c", checkout="d", main="c")
    assert v["kind"] == sd.UNMERGED_LOCAL


def test_case_d_peer_diverged_from_main():
    v = _classify(local="d", peer="x", checkout="d", main="d")
    assert v["kind"] == sd.UNMERGED_PEER and not v["sync_helps"]


def test_peer_commit_missing_from_a_fresh_fetch_is_unmerged_peer():
    v = _classify(local="d", peer="zzz", checkout="d", main="d")
    assert v["kind"] == sd.UNMERGED_PEER


def test_stale_main_is_unknown_not_a_guess():
    """A failed fetch must not let the tracking ref stand in for main."""
    v = _classify(local="d", peer="b", checkout="d", main="d", fetch_ok=False)
    assert v["kind"] == sd.UNKNOWN and not v["sync_helps"]


def test_restart_needed_is_named_even_when_main_is_unreachable():
    v = _classify(local="b", checkout="d", peer="d", main=None, fetch_ok=False)
    assert v["kind"] == sd.RESTART_LOCAL


def test_unreadable_checkout_is_unknown():
    assert _classify(local="b", peer="d", checkout="unknown")["kind"] == sd.UNKNOWN


def test_running_build_missing_from_checkout_is_unknown():
    assert _classify(local="zzz", peer="d", checkout="d", match=False)["kind"] == sd.UNKNOWN


# ---------------------------------------------------------------- git binding
def _git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
        creationflags=NO_WINDOW,
    )


@pytest.fixture()
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    # A throwaway repo must not run the machine's global commit hooks.
    _git(tmp_path, "config", "core.hooksPath", str(tmp_path / "no-hooks"))
    shas = []
    for i in range(3):
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", f"c{i}")
        shas.append(sd.resolve_git_sha(tmp_path))
    return tmp_path, shas


def test_make_compare_against_real_history(repo):
    root, (first, _second, third) = repo
    compare = sd.make_compare(root)
    assert compare(first, first) == sd.Relation(sd.SAME)
    assert compare(first, third) == sd.Relation(sd.BEHIND, 2)
    assert compare(third, first) == sd.Relation(sd.AHEAD, 2)
    assert compare(first, "deadbee") is None


def test_make_compare_reports_diverged(repo):
    root, (first, second, third) = repo
    _git(root, "checkout", "-q", "-b", "side", first)
    _git(root, "commit", "-q", "--allow-empty", "-m", "side")
    side = sd.resolve_git_sha(root)
    assert sd.make_compare(root)(side, third) == sd.Relation(sd.DIVERGED)


def test_fetch_failure_is_reported_and_retried_sooner(repo, monkeypatch):
    """No origin configured: the fetch fails, reads as not current, and is not cached for the full window."""
    root, _ = repo
    monkeypatch.setattr(sd, "_fetch_state", {"at": 0.0, "ok": 0.0})
    assert sd.refresh_origin_main(root) is False
    assert sd._fetch_state["ok"] == 0.0
    calls = []
    real = sd._git
    monkeypatch.setattr(sd, "_git", lambda *a, **k: calls.append(a) or real(*a, **k))
    sd.refresh_origin_main(root)
    assert calls == [], "a failure inside the retry window must not spawn another fetch"


def test_diagnose_skips_git_for_a_matching_pair(monkeypatch):
    monkeypatch.setattr(sd, "_git", lambda *a, **k: pytest.fail("git must not run"))
    v = sd.diagnose(peer_name="P", git_sha_match=True, local_running="a", peer_running="a")
    assert v["kind"] == sd.IN_SYNC
