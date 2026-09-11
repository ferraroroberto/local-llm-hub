"""run-weekly.bat's bi-weekly gate (#558).

The frontier-refresh job fires weekly and skips alternate weeks itself. It
used to read the week parity through a cmd ``for /f`` capture of a bare
``powershell -Command``; whenever that capture came back empty (PowerShell
not resolvable on PATH, or a non-interactive nested caller) the off-week
guard fell through and the job ran every week, exiting 0. The parity now
comes back as ``week_parity.ps1``'s exit code, from the absolute
``powershell.exe`` path, and an unrecognised code fails the job instead of
running it.

These drive the real batch file with a stub ``claude.cmd`` first on PATH, so
no Claude session ever starts.
"""

from __future__ import annotations

import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.no_window import NO_WINDOW

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows batch file")

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "frontier-refresh"
STUB_MARKER = "STUB_CLAUDE_RAN"


def _run_bat(bat: Path, stub_dir: Path) -> subprocess.CompletedProcess:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    env = dict(os.environ)
    # Deliberately no WindowsPowerShell dir on PATH: the gate must not depend
    # on resolving a bare `powershell` name.
    env["PATH"] = os.pathsep.join([str(stub_dir), rf"{system_root}\System32", system_root])
    return subprocess.run(
        [os.environ.get("COMSPEC", rf"{system_root}\System32\cmd.exe"), "/c", str(bat)],
        capture_output=True,
        encoding="oem",
        errors="replace",
        env=env,
        timeout=120,
        creationflags=NO_WINDOW,
    )


@pytest.fixture
def stub_dir(tmp_path: Path) -> Path:
    d = tmp_path / "stub"
    d.mkdir()
    (d / "claude.cmd").write_text(f"@echo {STUB_MARKER}\r\n", encoding="ascii")
    return d


def _is_on_week(today: datetime.date) -> bool:
    return ((today - datetime.date(2026, 1, 5)).days // 7) % 2 == 0


def test_gate_follows_epoch_parity_without_powershell_on_path(stub_dir: Path) -> None:
    result = _run_bat(SKILL_DIR / "run-weekly.bat", stub_dir)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert (STUB_MARKER in output) == _is_on_week(datetime.date.today()), output


def test_unknown_parity_fails_the_job_without_running(tmp_path: Path, stub_dir: Path) -> None:
    # The batch file alone, without week_parity.ps1 beside it: PowerShell
    # exits with its own error code, which must not be read as a verdict.
    bat = tmp_path / "run-weekly.bat"
    shutil.copy(SKILL_DIR / "run-weekly.bat", bat)

    result = _run_bat(bat, stub_dir)
    output = result.stdout + result.stderr

    assert result.returncode == 2, output
    assert "week parity unknown" in output
    assert STUB_MARKER not in output
