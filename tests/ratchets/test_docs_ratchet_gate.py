"""The gate's docs-ratchet stage maps the vendored script's exit codes.

`scripts/docs_ratchet.py` exits 3 when it could not confirm a doc (an unclosed
code fence or HTML comment, a non-UTF-8 file, unusable git): part of the tree
was not checked. These tests pin that 3 fails as NOT CONFIRMED -- never a pass,
never a skip -- that the pre-ship gate calls the wrapper that guarantees it
rather than the script, and that the real script, run through the wrapper on a
throwaway git repo, turns a new issue reference red by file name. No network, no
model and no live backend.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts import docs_ratchet_gate as gate
from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDORED = REPO_ROOT / "scripts" / "docs_ratchet.py"

_FLEET = '[docs_ratchet]\nenabled = true\nexclude = ["docs/reports/**"]\n'


def _fake_script(tmp_path: Path, code: int, message: str = "") -> Path:
    script = tmp_path / "fake_ratchet.py"
    script.write_text(
        f"import sys\nprint({message!r})\nsys.exit({code})\n", encoding="utf-8"
    )
    return script


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True,
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
    )


def _throwaway_repo(tmp_path: Path, readme: str) -> Path:
    """A git repo holding a copy of the vendored script, a `[docs_ratchet]`
    table and `readme`, with the baseline taken from the README as given."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    script = root / "scripts" / "docs_ratchet.py"
    shutil.copyfile(VENDORED, script)
    (root / ".fleet.toml").write_text(_FLEET, encoding="utf-8")
    (root / "README.md").write_text(readme, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    subprocess.run(
        [sys.executable, str(script), "--init"], cwd=root, check=True,
        capture_output=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
    )
    return script


@pytest.mark.parametrize(
    ("code", "ok"), [(0, True), (1, False), (2, False), (3, False), (4, False), (-1, False)]
)
def test_only_exit_zero_passes(code: int, ok: bool) -> None:
    assert gate.classify_exit(code)[0] is ok


def test_exit_three_is_its_own_not_confirmed_state() -> None:
    labels = {code: gate.classify_exit(code)[1] for code in (1, 2, 3)}
    assert "NOT CONFIRMED" in labels[3]
    assert "skip" not in labels[3].lower()
    assert len(set(labels.values())) == 3  # distinct conditions, distinct messages


def test_the_wrapper_runs_a_script_that_exits_three_as_a_failed_stage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert gate.run(_fake_script(tmp_path, 3, "a doc did not close")) == 1
    out = capsys.readouterr().out
    assert "a doc did not close" in out
    assert out.rstrip().splitlines()[-1].startswith("FAIL: docs ratchet: NOT CONFIRMED")


def test_an_added_issue_reference_fails_the_gate_by_file_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _throwaway_repo(tmp_path, "# Title\n\nPlain prose.\n")
    assert gate.run(script) == 0
    capsys.readouterr()

    (script.parent.parent / "README.md").write_text(
        "# Title\n\nPlain prose, fixed in #123.\n", encoding="utf-8"
    )
    assert gate.run(script) == 1
    out = capsys.readouterr().out
    assert "README.md" in out
    assert "#123" in out
    assert out.rstrip().splitlines()[-1].startswith("FAIL")


def test_a_doc_the_script_cannot_confirm_fails_the_gate_as_not_confirmed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The regression for naive wiring: an unclosed fence makes the real script
    exit 3, which a stage that only checks for a printed "FAIL" or reads a
    nonzero exit as "nothing new" would let through."""
    script = _throwaway_repo(tmp_path, "# Title\n\nPlain prose.\n")
    (script.parent.parent / "README.md").write_text(
        "# Title\n\n```\nnever closed\n", encoding="utf-8"
    )
    direct = subprocess.run(
        [sys.executable, str(script)], cwd=script.parent.parent, check=False,
        capture_output=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
    )
    assert direct.returncode == gate.EXIT_NOT_CONFIRMED
    assert gate.run(script) == 1
    assert "NOT CONFIRMED" in capsys.readouterr().out


def test_the_pre_ship_gate_calls_the_wrapper_not_the_script() -> None:
    gate_ps1 = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(
        encoding="utf-8"
    )
    lines = gate_ps1.splitlines()
    stage = [i for i, line in enumerate(lines) if line.startswith('Step "docs ratchet"')]
    assert len(stage) == 1
    assert stage[0] > next(
        i for i, line in enumerate(lines) if line.startswith('Step "static ratchet"')
    )
    body = lines[stage[0] + 1 : lines.index("}", stage[0])]
    calls = [line for line in body if line.strip().startswith("&")]
    assert len(calls) == 1
    assert "-m scripts.docs_ratchet_gate" in calls[0]
    assert "docs_ratchet.py" not in calls[0]


def test_the_ratchet_is_declared_and_its_script_is_vendored() -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    assert fleet["docs_ratchet"]["enabled"] is True
    baseline = REPO_ROOT / fleet["docs_ratchet"]["baseline"]
    assert baseline.is_file()
    entry = fleet["vendored"]["docs_ratchet"]
    assert entry["dest"] == "scripts/docs_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()
