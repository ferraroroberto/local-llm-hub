"""The gate's e2e routing stage never narrows on a verdict it cannot read (#843).

`scripts/e2e_route.py` wraps the vendored `classify_e2e.py`. These tests pin the
fail-safe: a classifier that exits non-zero, prints nothing, prints an unknown
tier or routes a run tier with no target yields the full suite with the reason
spelled out -- while a readable `skip` verdict (a docs-only diff) is honoured
and a `full` path stays full. They use fake classifiers in a temp directory and
the real `.fleet.toml` rules through `classify`, so no browser, no network and
no live backend is involved.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts import e2e_route
from scripts.classify_e2e import classify, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "scripts" / "verify-before-ship.ps1"


def _fake_classifier(tmp_path: Path, stdout: str, code: int = 0, stderr: str = "") -> Path:
    script = tmp_path / "fake_classify.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({code})\n",
        encoding="utf-8",
    )
    return script


def _assert_full_fail_safe(routing: dict[str, str], reason_has: str) -> None:
    assert routing["E2E_TIER"] == "full"
    assert routing["E2E_PYTEST_TARGET"] == "tests/e2e"
    assert routing["E2E_BROWSERS"] == "chromium"
    assert reason_has in routing["E2E_REASON"]
    assert "full suite (fail-safe)" in routing["E2E_REASON"]


def test_docs_only_verdict_skips_with_the_routers_reason(tmp_path: Path) -> None:
    out = (
        "E2E_TIER=skip\nE2E_BROWSERS=\nE2E_PYTEST_TARGET=\n"
        "E2E_REASON=README.md: markdown anywhere\n"
    )
    routing = e2e_route.route(_fake_classifier(tmp_path, out))
    assert routing["E2E_TIER"] == "skip"
    assert routing["E2E_PYTEST_TARGET"] == ""
    assert routing["E2E_REASON"] == "README.md: markdown anywhere"


def test_full_verdict_passes_through(tmp_path: Path) -> None:
    out = (
        "E2E_TIER=full\nE2E_BROWSERS=\nE2E_PYTEST_TARGET=tests/e2e\n"
        "E2E_REASON=app_web/static/js/models.js: the admin PWA\n"
    )
    routing = e2e_route.route(_fake_classifier(tmp_path, out))
    assert routing["E2E_TIER"] == "full"
    assert routing["E2E_PYTEST_TARGET"] == "tests/e2e"
    assert routing["E2E_BROWSERS"] == "chromium"  # a verdict naming none means the gate's browser


def test_surface_verdict_keeps_its_slice_and_browsers(tmp_path: Path) -> None:
    out = (
        "E2E_TIER=surface\nE2E_BROWSERS=chromium\n"
        "E2E_PYTEST_TARGET=tests/e2e/test_a.py tests/e2e/test_b.py\nE2E_REASON=one surface\n"
        "E2E_SURFACE=models\n"
    )
    routing = e2e_route.route(_fake_classifier(tmp_path, out))
    assert routing["E2E_TIER"] == "surface"
    assert routing["E2E_PYTEST_TARGET"] == "tests/e2e/test_a.py tests/e2e/test_b.py"


def test_router_error_exit_runs_full_and_says_why(tmp_path: Path) -> None:
    # Even a stdout that would have said skip is ignored when the exit is non-zero.
    out = "E2E_TIER=skip\nE2E_REASON=looked fine\n"
    routing = e2e_route.route(
        _fake_classifier(tmp_path, out, code=2, stderr="Traceback\nValueError: boom\n")
    )
    _assert_full_fail_safe(routing, "classifier exited 2: ValueError: boom")


@pytest.mark.parametrize("stdout", ["", "no key value lines here\n", "e2e_tier=skip\n"])
def test_unparseable_output_runs_full(tmp_path: Path, stdout: str) -> None:
    routing = e2e_route.route(_fake_classifier(tmp_path, stdout))
    _assert_full_fail_safe(routing, "no E2E_TIER")


def test_unknown_tier_runs_full(tmp_path: Path) -> None:
    routing = e2e_route.route(_fake_classifier(tmp_path, "E2E_TIER=nothing\n"))
    _assert_full_fail_safe(routing, "unknown tier 'nothing'")


@pytest.mark.parametrize("tier", ["static", "surface", "full"])
def test_run_tier_without_a_target_runs_full(tmp_path: Path, tier: str) -> None:
    routing = e2e_route.route(_fake_classifier(tmp_path, f"E2E_TIER={tier}\nE2E_PYTEST_TARGET=\n"))
    _assert_full_fail_safe(routing, f"routed {tier} with no pytest target")


def test_missing_classifier_runs_full(tmp_path: Path) -> None:
    # A path that is not a script: python exits 2 (cannot open file).
    routing = e2e_route.route(tmp_path / "does_not_exist.py")
    _assert_full_fail_safe(routing, "classifier exited")


def test_timeout_runs_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(e2e_route, "RUN_TIMEOUT_S", 1)
    hang = tmp_path / "hang.py"
    hang.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    _assert_full_fail_safe(e2e_route.route(hang), "did not finish in 1s")


def test_a_reason_cannot_forge_a_second_e2e_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        e2e_route, "route",
        lambda: {"E2E_TIER": "skip", "E2E_BROWSERS": "chromium", "E2E_PYTEST_TARGET": "",
                 "E2E_REASON": "a\nE2E_TIER=skip"},
    )
    assert e2e_route.main() == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 4
    assert sum(1 for ln in lines if ln.startswith("E2E_TIER=")) == 1


def test_real_declared_rules_route_docs_to_skip_and_the_pwa_to_full() -> None:
    cfg = load_config(REPO_ROOT / ".fleet.toml")
    assert classify(["README.md", "docs/layout.md", "CLAUDE.md"], cfg).tier == "skip"
    assert classify(["app_web/static/js/models.js", "README.md"], cfg).tier == "full"
    # The empty diff and an unclassified path are the router's own fail-safe.
    assert classify([], cfg).tier == "full"
    assert classify(["brand_new_dir/thing.xyz"], cfg).tier == "full"


def test_gate_routes_e2e_through_the_wrapper_not_a_bare_suite() -> None:
    gate = GATE.read_text(encoding="utf-8")
    assert "scripts.e2e_route" in gate
    assert "--junitxml=data/e2e-junit.xml" in gate  # /e2e-audit reads it whenever the suite runs
    # The unconditional whole-suite call this issue removed must not come back.
    assert not re.search(r"pytest tests/e2e -q --browser chromium --junitxml", gate.split("Step \"byte-compile\"")[1])
