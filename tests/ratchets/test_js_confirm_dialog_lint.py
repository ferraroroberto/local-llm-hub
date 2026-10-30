"""Lint: the shared confirm dialog is reached only through `confirmDialog()`.

Models, Machines and Diagnostics once each hung their own click listener on the
one dialog's buttons and told them apart with a `dataset.for` discriminator plus
a capture-phase `stopImmediatePropagation`, which worked only by listener
ordering. `confirm_dialog.js` now owns those elements; a tab that touches them
directly would bring that coupling back.
"""

from __future__ import annotations

import re

from tests.ratchets._js_lint_support import REPO_ROOT, tracked_js

_OWNERS = {"confirm_dialog.js", "state.js"}
# `machinesConfirm*` was the dialog's old, Machines-owned name.
_DIALOG_REF_RE = re.compile(r"els\.(confirmDialog|machinesConfirm)")


def test_confirm_dialog_elements_are_touched_only_by_the_helper():
    offenders: dict[str, list[int]] = {}
    for path in tracked_js():
        if path.name in _OWNERS:
            continue
        lines = [
            i for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if _DIALOG_REF_RE.search(line)
        ]
        if lines:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = lines

    assert not offenders, (
        "els.confirmDialog* used outside confirm_dialog.js -- await "
        "confirmDialog({title, body, confirmLabel}) instead:\n"
        + "\n".join(f"  {rel}: lines {lns}" for rel, lns in offenders.items())
    )
