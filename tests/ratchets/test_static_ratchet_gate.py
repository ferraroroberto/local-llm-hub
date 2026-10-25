"""The gate's static-ratchet stage maps the vendored script's exit codes (#838).

`scripts/static_ratchet.py` exits 3 when a detector could not run (no `npx`, no
ruff, a timeout): nothing was checked. These tests pin that 3 fails as NOT
CONFIRMED -- never a pass, never a skip -- and that the pre-ship gate calls the
wrapper that guarantees it rather than the script. They run fake scripts in a
temp directory: no network, no `npx`, no model and no live backend.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

from scripts import static_ratchet_gate as gate

REPO_ROOT = Path(__file__).resolve().parents[2]


def _fake_script(tmp_path: Path, code: int, message: str = "") -> Path:
    script = tmp_path / "fake_ratchet.py"
    script.write_text(
        f"import sys\nprint({message!r})\nsys.exit({code})\n", encoding="utf-8"
    )
    return script


def test_only_exit_zero_passes() -> None:
    assert gate.classify_exit(0)[0] is True
    for code in (1, 2, 3, 4, -1):
        assert gate.classify_exit(code)[0] is False, code


def test_exit_three_is_not_confirmed_and_never_a_skip() -> None:
    ok, label = gate.classify_exit(3)
    assert not ok
    assert "NOT CONFIRMED" in label
    assert "skip" not in label.lower()


@pytest.mark.parametrize(("code", "expected"), [(0, 0), (1, 1), (2, 1), (3, 1)])
def test_run_exits_nonzero_for_every_script_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int, expected: int
) -> None:
    got = gate.run(_fake_script(tmp_path, code, "from the script"))
    out = capsys.readouterr().out
    assert got == expected
    assert "from the script" in out  # the script's own finding is echoed
    assert out.rstrip().splitlines()[-1].startswith("OK" if code == 0 else "FAIL")


def test_run_reports_a_detector_that_could_not_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert gate.run(_fake_script(tmp_path, 3, "npx was not found")) == 1
    out = capsys.readouterr().out
    assert "npx was not found" in out
    assert "NOT CONFIRMED" in out


def test_run_treats_a_timeout_and_an_unstartable_script_as_not_confirmed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def hang(*_a: object, **_k: object) -> None:
        raise gate.subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(gate.subprocess, "run", hang)
    assert gate.run(_fake_script(tmp_path, 0)) == 1
    assert "NOT CONFIRMED" in capsys.readouterr().out

    def refuse(*_a: object, **_k: object) -> None:
        raise OSError("no interpreter")

    monkeypatch.setattr(gate.subprocess, "run", refuse)
    assert gate.run(_fake_script(tmp_path, 0)) == 1
    assert "NOT CONFIRMED" in capsys.readouterr().out


def test_the_wrapper_runs_under_the_venv_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    real = gate.subprocess.run

    def spy(argv: list[str], **kwargs: object) -> object:
        seen.append(argv)
        return real(argv, **kwargs)

    monkeypatch.setattr(gate.subprocess, "run", spy)
    gate.run(_fake_script(tmp_path, 0))
    assert seen[0][0] == sys.executable  # the python that has ruff, not PATH's


def test_the_pre_ship_gate_calls_the_wrapper_not_the_script() -> None:
    gate_ps1 = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(
        encoding="utf-8"
    )
    lines = gate_ps1.splitlines()
    stage = [i for i, line in enumerate(lines) if line.startswith('Step "static ratchet"')]
    assert len(stage) == 1
    body = lines[stage[0] + 1 : lines.index("}", stage[0])]
    calls = [line for line in body if line.strip().startswith("&")]
    assert len(calls) == 1
    assert "-m scripts.static_ratchet_gate" in calls[0]
    assert "static_ratchet.py" not in calls[0]


def test_the_ratchet_is_declared_and_its_script_is_vendored() -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    assert fleet["static_ratchet"]["enabled"] is True
    baseline = REPO_ROOT / fleet["static_ratchet"]["baseline"]
    assert baseline.is_file()
    entry = fleet["vendored"]["static_ratchet"]
    assert entry["dest"] == "scripts/static_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()
