"""The gate's conventions-ratchet stage fails by rule name and never passes on doubt.

`scripts/conventions_ratchet.py` exits 3 when it could not confirm the tree (git
unusable, a tracked `.py` file that does not parse): part of the tree was not
checked. These tests pin that the real script, run on a throwaway git repo,
turns a seeded violation red by rule name, that exit 3 is its own state, and
that the pre-ship gate calls the script directly so any non-zero exit fails the
stage. No network, no model and no live backend.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDORED = REPO_ROOT / "scripts" / "conventions_ratchet.py"

EXIT_NOT_CONFIRMED = 3

_FLEET = (
    "[conventions_ratchet]\n"
    "enabled = true\n"
    'rules = ["no-window", "logging-not-print", "no-drive-path"]\n'
    'print_paths = ["src/"]\n'
)
_CLEAN = '"""Clean library module."""\n\nVALUE = 1\n'


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, check=False, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
    )


def _throwaway_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo holding a copy of the vendored script, a `[conventions_ratchet]`
    table and one clean library module, with the baseline taken as it stands."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "src").mkdir()
    script = root / "scripts" / "conventions_ratchet.py"
    shutil.copyfile(VENDORED, script)
    (root / ".fleet.toml").write_text(_FLEET, encoding="utf-8")
    module = root / "src" / "lib.py"
    module.write_text(_CLEAN, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    init = _run([sys.executable, str(script), "--init"], root)
    assert init.returncode == 0, init.stdout + init.stderr
    return script, module


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True,
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
    )


def _check(script: Path) -> subprocess.CompletedProcess[str]:
    return _run([sys.executable, str(script)], script.parent.parent)


def test_a_clean_tree_passes(tmp_path: Path) -> None:
    script, _ = _throwaway_repo(tmp_path)
    assert _check(script).returncode == 0


@pytest.mark.parametrize(
    ("rule", "line"),
    [
        ("logging-not-print", 'def f():\n    print("diagnostic")\n'),
        ("no-drive-path", 'DATA = "E:/data/out"\n'),
        ("no-window", 'import subprocess\n\n\ndef f():\n    subprocess.run(["tool"])\n'),
    ],
)
def test_a_seeded_violation_fails_the_stage_by_rule_name(
    tmp_path: Path, rule: str, line: str
) -> None:
    script, module = _throwaway_repo(tmp_path)
    module.write_text(_CLEAN + "\n" + line, encoding="utf-8")
    _git(script.parent.parent, "add", "-A")
    result = _check(script)
    assert result.returncode == 1
    assert f"[{rule}]" in result.stdout + result.stderr
    assert "src/lib.py" in result.stdout + result.stderr


def test_a_file_the_script_cannot_parse_is_not_confirmed(tmp_path: Path) -> None:
    """A file that does not parse may hold a violation; skipping it would report
    a clean tree the scanner never saw, so exit 3 is its own failing state."""
    script, module = _throwaway_repo(tmp_path)
    module.write_text("def broken(:\n", encoding="utf-8")
    _git(script.parent.parent, "add", "-A")
    result = _check(script)
    assert result.returncode == EXIT_NOT_CONFIRMED
    assert "lib.py" in result.stdout + result.stderr


def test_the_pre_ship_gate_calls_the_script_after_dead_code_and_before_unit_tests() -> None:
    lines = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(
        encoding="utf-8"
    ).splitlines()

    def step(name: str) -> int:
        found = [i for i, line in enumerate(lines) if line.startswith(f'Step "{name}"')]
        assert len(found) == 1, name
        return found[0]

    stage = step("conventions ratchet")
    assert step("dead-code ratchet") < stage < step("pytest (unit)")
    body = lines[stage + 1 : lines.index("}", stage)]
    calls = [line.strip() for line in body if line.strip().startswith("&")]
    assert calls == ["& $Python scripts/conventions_ratchet.py"]


def test_the_ratchet_is_declared_and_its_script_is_vendored() -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    table = fleet["conventions_ratchet"]
    assert table["enabled"] is True
    assert (REPO_ROOT / table["baseline"]).is_file()
    # No shared helper exists for either, so the helper-gated rules stay off.
    assert not {"secret-compare", "git-wrapper"} & set(table["rules"])
    entry = fleet["vendored"]["conventions_ratchet"]
    assert entry["dest"] == "scripts/conventions_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()
    rules = fleet["e2e"]["rule"]
    assert {"tier": "none", "path": table["baseline"]} in rules
