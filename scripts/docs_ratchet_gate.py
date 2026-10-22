r"""The gate's docs-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.docs_ratchet_gate

`scripts/docs_ratchet.py` (vendored, never edited here) exits 0 on a pass, 1
when a README or docs file gained a history marker or grew past its size
baseline, 2 on a bad table or baseline and 3 when it could not confirm a file
(git unusable, a non-UTF-8 file, a fence or HTML comment that never closes).
Exit 3 means part of the tree was not checked, so it must fail the gate as its
own state and never read as a pass or a skip. The run-and-map loop is
`static_ratchet_gate.run`; this module only supplies the verdict labels, and
`verify-before-ship.ps1` calls this wrapper, never the script.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import static_ratchet_gate as _shared

REPO_ROOT = _shared.REPO_ROOT
SCRIPT = REPO_ROOT / "scripts" / "docs_ratchet.py"

EXIT_PASS, EXIT_NEW, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# A Markdown line scan over a few dozen files takes well under a second; this
# only has to outlast a wedged `git ls-files` so the hang is reported as NOT
# CONFIRMED instead of stalling the gate.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_NEW: "NEW history marker, README over budget or missing baseline",
    EXIT_CONFIG: "bad [docs_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a doc could not be read, so it was not checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no new history marker in the README or docs, README within budget"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run the vendored script and return the gate's exit code."""
    return _shared.run(
        script, stage="docs ratchet", classify=classify_exit, timeout_s=RUN_TIMEOUT_S
    )


if __name__ == "__main__":
    sys.exit(run())
