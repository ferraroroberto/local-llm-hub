"""File, update and close the GitHub issues a nightly Mac run calls for.

The Mac workflow holds no token and makes no outbound call, so this trusted
host is the one place a red ``main`` becomes an issue. Three kinds, each kept to
exactly one open issue per subject (deduped by a hidden marker in the body):

* ``ci-red-main``: one per repo, opened or commented on by a red night and
  closed by the next green one;
* ``flaky-test``: one per test id that flaked on three nights within fourteen.

Every function ends on a state word and never folds "gh could not confirm" into
success: ``unknown`` is its own result and the caller retries it. What reaches
GitHub is a SHA, leg names, counts and test node ids, nothing else. The ids come
from adopter code, so they are shown only if they fit a conservative pattern
and only inside a code fence.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from typing import Callable, List, Optional, Sequence, Tuple

from ..no_window import NO_WINDOW

OWNER_ENV = "MAC_RESULTS_GH_OWNER"
DEFAULT_OWNER = "ferraroroberto"
GH_TIMEOUT_S = 60

RED_LABEL = "ci-red-main"
FLAKY_LABEL = "flaky-test"
RED_TITLE = "main's nightly gate is red"
_LABELS = {
    RED_LABEL: ("B60205", "The nightly gate on main is red"),
    FLAKY_LABEL: ("FBCA04", "A test that passes only on retry"),
}

# Opened, commented, closed, nothing to do, already says this, or not confirmed.
CREATED, COMMENTED, CLOSED, NONE, UNCHANGED, UNKNOWN = "created", "commented", "closed", "none", "unchanged", "unknown"

# gh's arguments in, its stdout out; None when it could not run or exited non-zero.
Gh = Callable[[Sequence[str]], Optional[str]]


def make_gh() -> Gh:
    def gh(args: Sequence[str]) -> Optional[str]:
        try:
            out = subprocess.run(
                ["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                stdin=subprocess.DEVNULL, timeout=GH_TIMEOUT_S, creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout if out.returncode == 0 else None

    return gh


def slug_for(repo: str) -> str:
    return f"{os.environ.get(OWNER_ENV, '').strip() or DEFAULT_OWNER}/{repo}"


def _marker(kind: str, text: str) -> str:
    return f"<!-- {kind}:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]} -->"


def ensure_label(gh: Gh, slug: str, name: str) -> bool:
    """Create the label, or refresh it if present (``--force``): idempotent."""
    color, description = _LABELS[name]
    return gh(["label", "create", name, "--repo", slug, "--color", color, "--description", description, "--force"]) is not None


def _open_issues(gh: Gh, slug: str, label: str) -> Optional[List[dict]]:
    listing = gh(["issue", "list", "--repo", slug, "--label", label, "--state", "open",
                  "--json", "number,title,body", "--limit", "100"])
    if listing is None:
        return None
    try:
        issues = json.loads(listing)
    except ValueError:
        return None
    return [i for i in issues if isinstance(i, dict) and isinstance(i.get("number"), int)] if isinstance(issues, list) else None


def _fence(ids: Sequence[str]) -> str:
    return "```\n" + "\n".join(ids) + "\n```"


def red_body(sha: str, legs: dict, ids: Sequence[str], withheld: int) -> str:
    failing = [name for name, result in legs.items() if result == "fail"]
    lines = [
        f"The nightly gate on `main` is red at `{sha}`.",
        "",
        "Failing legs: " + (", ".join(failing) or "none named") + ".",
        "",
    ]
    if ids:
        lines += [f"Failing tests ({len(ids)}):", "", _fence(ids), ""]
    else:
        lines += ["No failing test ids could be read for this night.", ""]
    if withheld:
        lines += [f"{withheld} further id(s) were left out (outside the allowed characters or over the cap).", ""]
    lines += ["- [ ] The next nightly run is green.", "", "Logs, traces and screenshots stay on the runner host."]
    return "\n".join(lines)


def file_red(gh: Gh, repo: str, sha: str, legs: dict, ids: Sequence[str], withheld: int) -> Tuple[str, str]:
    """Open the repo's one ``ci-red-main`` issue, or comment on it when the SHA or the
    failing set changed. Returns ``(state, detail)``."""
    slug = slug_for(repo)
    if not ensure_label(gh, slug, RED_LABEL):
        return UNKNOWN, "gh could not create the label"
    open_issues = _open_issues(gh, slug, RED_LABEL)
    if open_issues is None:
        return UNKNOWN, "gh could not list issues"
    body_text = red_body(sha, legs, ids, withheld)
    mark = _marker(RED_LABEL, json.dumps([sha, sorted(legs.items()), list(ids)]))
    body = f"{body_text}\n\n{mark}"
    if not open_issues:
        url = gh(["issue", "create", "--repo", slug, "--title", RED_TITLE, "--body", body,
                  "--label", "bug", "--label", RED_LABEL, "--assignee", "@me"])
        return (CREATED, url.strip()) if url else (UNKNOWN, "gh could not create the issue")
    number = str(open_issues[0]["number"])
    view = gh(["issue", "view", number, "--repo", slug, "--json", "body,comments"])
    if view is None:
        return UNKNOWN, "gh could not read the open issue"
    if mark in view:
        return UNCHANGED, f"#{number}"
    done = gh(["issue", "comment", number, "--repo", slug, "--body", body])
    return (COMMENTED, f"#{number}") if done is not None else (UNKNOWN, "gh could not comment")


def close_green(gh: Gh, repo: str, sha: str) -> Tuple[str, str]:
    """Comment on and close every open ``ci-red-main`` issue of the repo."""
    slug = slug_for(repo)
    open_issues = _open_issues(gh, slug, RED_LABEL)
    if open_issues is None:
        return UNKNOWN, "gh could not list issues"
    closed = []
    for issue in open_issues:
        number = str(issue["number"])
        done = gh(["issue", "close", number, "--repo", slug, "--comment", f"The nightly gate is green at `{sha}`."])
        if done is None:
            return UNKNOWN, f"gh could not close #{number}"
        closed.append(f"#{number}")
    return (CLOSED, " ".join(closed)) if closed else (NONE, "no open ci-red-main issue")


def flaky_issue_title(test_id: str) -> str:
    return f"flaky test: {test_id}"


def file_flaky(gh: Gh, repo: str, test_id: str, nights: int, window_days: int) -> Tuple[str, str]:
    """Open the one ``flaky-test`` issue for the id unless an open one carries its marker."""
    slug = slug_for(repo)
    if not ensure_label(gh, slug, FLAKY_LABEL):
        return UNKNOWN, "gh could not create the label"
    open_issues = _open_issues(gh, slug, FLAKY_LABEL)
    if open_issues is None:
        return UNKNOWN, "gh could not list issues"
    mark = _marker(FLAKY_LABEL, test_id)
    for issue in open_issues:
        if mark in str(issue.get("body") or ""):
            return UNCHANGED, f"#{issue['number']}"
    body = "\n".join([
        f"This test passed only on the retry on {nights} nightly runs within {window_days} days.",
        "",
        _fence([test_id]),
        "",
        "- [ ] The cause is found and the test is stable, or it is quarantined with a reason.",
        "",
        "Logs, traces and screenshots stay on the runner host.",
        "",
        mark,
    ])
    url = gh(["issue", "create", "--repo", slug, "--title", flaky_issue_title(test_id), "--body", body,
              "--label", "bug", "--label", FLAKY_LABEL, "--assignee", "@me"])
    return (CREATED, url.strip()) if url else (UNKNOWN, "gh could not create the issue")
