"""Shared helper for the hand-rolled JS lints under `tests/ratchets/` --
`_tracked_js()` lists every tracked `app_web/static/**/*.js` file, excluding
the vendored admin-SPA components (copies by design, same exclusion the
static ratchet uses, `.fleet.toml [static_ratchet]`). Three lints
(`test_js_silent_catch_lint.py`, `test_js_additive_expression_lint.py`,
`test_js_native_dialog_lint.py`) used to retype this verbatim; this is the
one home per the repo's own "three similar lines" convention.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]

_EXCLUDED_PREFIXES = ("app_web/static/_vendored/",)


def tracked_js() -> list[Path]:
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
