r"""The gate's static-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.static_ratchet_gate

`scripts/static_ratchet.py` (vendored, never edited here) exits 0 on a pass,
1 on a new finding, 2 on a bad table or baseline and 3 when a detector could
not run (no `npx`, no network on the first `npx`, ruff missing, a timeout).
Exit 3 means nothing was checked, so it must fail the gate as its own state and
never read as a pass or a skip. This wrapper is the one place that mapping
lives, so a test can pin it; `verify-before-ship.ps1` calls the wrapper, never
the script.

Exit 0 only when the script exited 0; every other outcome exits 1 with its
reason on the last line.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "static_ratchet.py"

EXIT_PASS, EXIT_NEW, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# The script bounds each of its two detectors at 600 s itself; this only has to
# outlast both so a hung child is reported by the script's own NOT CONFIRMED.
RUN_TIMEOUT_S = 1300

_FAIL_LABELS = {
    EXIT_NEW: "NEW finding or missing baseline",
    EXIT_CONFIG: "bad [static_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a detector could not run, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no new duplicate block or unused import/variable"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script` under this interpreter (the venv python, which has ruff),
    echo its output, then the verdict. Returns the gate's exit code."""
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
            timeout=RUN_TIMEOUT_S, check=False,
        )
        code, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code, output = EXIT_NOT_CONFIRMED, f"{script.name} did not finish in {RUN_TIMEOUT_S}s\n"
    except OSError as exc:
        code, output = EXIT_NOT_CONFIRMED, f"{script.name} could not start: {exc}\n"
    if output:
        sys.stdout.write(output if output.endswith("\n") else output + "\n")
    ok, label = classify_exit(code)
    print(f"{'OK' if ok else 'FAIL'}: static ratchet: {label}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
