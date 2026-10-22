r"""The gate's e2e routing stage: run the vendored classifier, never narrow on doubt.

    & .\.venv\Scripts\python.exe -m scripts.e2e_route

`scripts/classify_e2e.py` (vendored, never edited here) maps the branch diff to
`skip` / `static` / `surface` / `full` and prints `E2E_*=` lines. The gate may
only run *less* than the whole suite on a verdict it could actually read, so
this wrapper turns every way the classifier can fail to give one (non-zero
exit, timeout, no `E2E_TIER`, an unknown tier, a run tier with no pytest
target) into `full` with the reason spelled out. It is the one place that
mapping lives, so a test can pin it; `verify-before-ship.ps1` calls the
wrapper, never the classifier.

Always exits 0 and prints exactly the four `E2E_*=` lines on stdout.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parent.parent
CLASSIFIER = REPO_ROOT / "scripts" / "classify_e2e.py"

TIERS = frozenset({"skip", "static", "surface", "full"})
FULL_TARGET = "tests/e2e"
# The gate is Chromium-only (CLAUDE.md), so a verdict naming no browser means it.
DEFAULT_BROWSER = "chromium"

# The classifier shells out to git a handful of times; this only has to outlast
# a slow disk so a hang is reported rather than waited on.
RUN_TIMEOUT_S = 120


def _fail_safe(reason: str) -> dict[str, str]:
    """The whole suite, with why the classifier's verdict could not be used."""
    return {
        "E2E_TIER": "full",
        "E2E_BROWSERS": DEFAULT_BROWSER,
        "E2E_PYTEST_TARGET": FULL_TARGET,
        "E2E_REASON": f"{reason} -- running the full suite (fail-safe)",
    }


def parse_verdict(stdout: str) -> dict[str, str]:
    """The routing for one classifier stdout, or the fail-safe when unreadable."""
    kv: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.startswith("E2E_") and key.isupper():
            kv[key] = value
    tier = kv.get("E2E_TIER", "")
    if not tier:
        return _fail_safe("classifier printed no E2E_TIER")
    if tier not in TIERS:
        return _fail_safe(f"classifier printed unknown tier {tier!r}")
    target = kv.get("E2E_PYTEST_TARGET", "").strip()
    if tier != "skip" and not target:
        return _fail_safe(f"classifier routed {tier} with no pytest target")
    return {
        "E2E_TIER": tier,
        "E2E_BROWSERS": kv.get("E2E_BROWSERS", "").strip() or DEFAULT_BROWSER,
        "E2E_PYTEST_TARGET": "" if tier == "skip" else target,
        "E2E_REASON": kv.get("E2E_REASON", "").strip() or "(no reason given)",
    }


def route(classifier: Path = CLASSIFIER) -> dict[str, str]:
    """Run `classifier` under this interpreter and return the usable routing."""
    try:
        proc = subprocess.run(
            [sys.executable, str(classifier)],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
            timeout=RUN_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired:
        return _fail_safe(f"classifier did not finish in {RUN_TIMEOUT_S}s")
    except OSError as exc:
        return _fail_safe(f"classifier could not start: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        tail = f": {detail[-1]}" if detail else ""
        return _fail_safe(f"classifier exited {proc.returncode}{tail}")
    return parse_verdict(proc.stdout or "")


def main() -> int:
    # A reason is one line by construction; flatten any stray newline so it can
    # never forge a second E2E_ line for the PowerShell parser.
    for key, value in route().items():
        print(f"{key}={' '.join(value.split())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
