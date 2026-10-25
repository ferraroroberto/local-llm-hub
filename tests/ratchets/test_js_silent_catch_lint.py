"""Lint: a `try { ... } catch (x) { BODY }` statement under `app_web/static/`
must not have a completely empty `BODY` -- no comment, no statement.

This repo's own established idiom for an intentionally-swallowed exception
(private-mode storage, "already closed", autoplay blocked, ...) is a `catch`
whose body is just a one-line comment explaining *why* it's safe to ignore --
dozens of examples across `app_web/static/`. An empty `catch (_) {}` with no
comment reads identically to the naming eye but is the opposite: it hides a
real failure with zero trace (a tab-change listener throw would vanish with
no log). This test closes that gap -- a `catch` with no body content at all,
anywhere under `app_web/static/` -- so a regression of the same shape fails
here before it ships.

Scoped to the `try/catch` statement form only, not a promise `.catch(fn)`
handler (`.catch(function () {})` is this codebase's separate, pervasive, and
accepted fire-and-forget idiom for a poll that will just retry next tick).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]

# Vendored admin-SPA components are copies by design (same exclusion the
# static ratchet uses, .fleet.toml [static_ratchet]) -- not this lint's concern.
_EXCLUDED_PREFIXES = ("app_web/static/_vendored/",)

_CATCH_RE = re.compile(r"catch\s*\(([^)]*)\)\s*\{")


def _tracked_js() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "app_web/static/*.js", "app_web/static/**/*.js"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        creationflags=NO_WINDOW,
    ).stdout
    paths = sorted(set(p for p in out.splitlines() if p.strip()))
    return [
        REPO_ROOT / p
        for p in paths
        if not any(p.startswith(prefix) for prefix in _EXCLUDED_PREFIXES)
    ]


def _empty_catch_lines(text: str) -> list[int]:
    """Line numbers of `try/catch` statement catches with a wholly-empty body."""
    offenders = []
    for match in _CATCH_RE.finditer(text):
        # A promise handler's "catch argument" is a `function` expression;
        # a try/catch catch clause's parenthesized part is just a bound name
        # (or nothing). Skip the promise form.
        if "function" in match.group(1):
            continue
        # A promise `.catch(` is a member access -- the char right before
        # `catch` is a `.`; a try/catch catch clause never is.
        if text[max(0, match.start() - 1):match.start()] == ".":
            continue
        start = match.end()
        depth = 1
        i = start
        while depth > 0 and i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[start:i - 1]
        if body.strip() == "":
            offenders.append(text.count("\n", 0, match.start()) + 1)
    return offenders


def test_no_silent_catch_without_a_comment():
    offenders: dict[str, list[int]] = {}
    for path in _tracked_js():
        lines = _empty_catch_lines(path.read_text(encoding="utf-8"))
        if lines:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = lines

    assert not offenders, (
        "Empty `catch` block(s) with no comment and no logging -- either log "
        "the error (e.g. `console.error('<what failed>', err)`) or explain "
        "why it's safe to ignore with a one-line comment:\n"
        + "\n".join(f"  {rel}: lines {lns}" for rel, lns in offenders.items())
    )
