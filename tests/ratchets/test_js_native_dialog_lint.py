"""Lint: no `window.confirm(` / `window.alert(` / `window.prompt(` (or the
bare, unqualified form) anywhere under `app_web/static/`.

The fleet design system (`~/.claude/design.md`) requires every confirm,
rename or detail surface to use the vendored dialog (`_vendored/modal`) --
"Don't call the browser's `confirm()`, `prompt()` or `alert()`." A native
call renders unstyled, blocks the whole tab (no toast, no `<dialog>`
backdrop), and nothing else in this SPA uses it, so a stray call is always a
drift, not a legitimate exception. This test closes the gap `/design-review`
and the static ratchet don't cover (jscpd flags duplicate blocks, not a
single disallowed call), so a regression of the same shape fails here
before it ships.
"""

from __future__ import annotations

import re

from tests.ratchets._js_lint_support import REPO_ROOT, tracked_js

# Scoped to the explicit `window.` form -- that's the shape every actual
# violation in this codebase has taken, and prose in a comment or string
# ("the browser's confirm()") never carries the `window.` prefix, so this
# stays false-positive-free without needing a real JS comment/string parser.
_NATIVE_DIALOG_RE = re.compile(r"\bwindow\.(confirm|alert|prompt)\s*\(")


def _native_dialog_lines(text: str) -> list[int]:
    return [text.count("\n", 0, m.start()) + 1 for m in _NATIVE_DIALOG_RE.finditer(text)]


def test_no_native_browser_dialog():
    offenders: dict[str, list[int]] = {}
    for path in tracked_js():
        lines = _native_dialog_lines(path.read_text(encoding="utf-8"))
        if lines:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = lines

    assert not offenders, (
        "Native browser dialog call(s) -- route confirm/rename/detail through "
        "the vendored dialog (_vendored/modal), same as machines.js's "
        "machinesConfirmDialog, instead of window.confirm()/alert()/prompt():\n"
        + "\n".join(f"  {rel}: lines {lns}" for rel, lns in offenders.items())
    )
