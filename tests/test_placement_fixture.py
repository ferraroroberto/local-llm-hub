"""No placement edit the admin UI can push reaches a unit test (#564, #565).

The placement card commits ``host:`` / ``hosts:`` / ``startup:`` /
``idle_unload_minutes:`` edits straight to ``main`` (#424) without running the
suite. These tests make deliberately bad edits through the same editor that
write path uses (``config_write.edit_models_yaml``) to a copy of the pinned
config — never the committed one, which an edit may already have moved — and
show two things: the edits are real, and pinning undoes every one of them.
The last test reruns the modules such edits used to turn red against an
edited config, as a separate pytest process.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from src import config_write, host_profile
from tests._placement_fixture import (
    COMMITTED_CONFIG_ENV,
    PLACEMENT_FIXTURE_ROWS,
    PLACEMENT_KEYS,
    committed_config_path,
    pin_placement_text,
)

# One bad edit per placeable row, each away from its PLACEMENT_FIXTURE_ROWS
# entry, across every key the editor writes: moves, chain reorders and a
# changed cpu tier, eager <-> on_demand flips (one of them a VRAM overcommit
# the validator would refuse), idle timeouts changed and cleared, and a host
# added to the unowned glm row.
BAD_EDITS = {
    "flux1_local": (["tower"], "eager", None),
    "flux2_klein": (["tower"], "on_demand", 20),
    "qwen": (["tower"], "on_demand", 7),
    "glm": (["tower"], "eager", None),
    "qwen35_4b": (["mac-mini-m4"], "on_demand", 7),
    "gemma4_e4b": (["mac-mini-m4"], "eager", None),
    "gemma4_26b": (["tower"], "eager", None),
    "whisper": (["tower", "mac-mini-m4", {"id": "gaming", "cpu": True}], "eager", None),
    "whisper_translate": (["tower"], "on_demand", 7),
    "whisper_vanilla": (["tower"], "eager", None),
    "parakeet": (["tower", "mac-mini-m4"], "on_demand", 7),
    "chatterbox": (["mac-mini-m4"], "eager", None),
    "piper": (["mac-mini-m4"], "on_demand", 7),
    "orpheus": (["gaming", "tower"], "on_demand", 7),
    "kokoro": (["mac-mini-m4"], "on_demand", 45),
}

# The modules BAD_EDITS turned red (13 tests) before #565 pinned the lifecycle
# keys: placement asserts, startup-gated routing, VRAM budgets, and the YAML
# editor's line-exact tests.
AFFECTED_MODULES = [
    "tests/test_audio_translate_bridge.py",
    "tests/test_config_write.py",
    "tests/test_fleet_placement_router.py",
    "tests/test_models_router.py",
    "tests/test_on_demand.py",
]


def _chain(entries):
    return [
        {"id": e["id"], "cpu": bool(e.get("cpu"))} if isinstance(e, dict) else {"id": e, "cpu": False}
        for e in entries
    ]


def _edit(path: Path, model_ids) -> Path:
    """Apply ``BAD_EDITS`` for ``model_ids`` to ``path`` via the production editor."""
    for model_id in model_ids:
        chain, startup, idle = BAD_EDITS[model_id]
        assert config_write.edit_models_yaml(path, model_id, _chain(chain), startup, idle)
    return path


def _placement(text: str) -> dict:
    rows = yaml.safe_load(text)["models"]
    return {mid: {k: row[k] for k in PLACEMENT_KEYS if k in row} for mid, row in rows.items()}


@pytest.fixture
def pinned_copy(tmp_path) -> Path:
    cfg = tmp_path / "models.yaml"
    cfg.write_text(host_profile.CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return cfg


def test_every_test_reads_the_pinned_config():
    assert host_profile.CONFIG_PATH != committed_config_path()
    assert config_write.CONFIG_PATH == host_profile.CONFIG_PATH
    pinned = host_profile.CONFIG_PATH.read_text(encoding="utf-8")
    assert pinned == pin_placement_text(committed_config_path().read_text(encoding="utf-8"))
    placed = {mid: keys for mid, keys in _placement(pinned).items() if keys}
    assert placed == PLACEMENT_FIXTURE_ROWS


@pytest.mark.parametrize("model_id", sorted(BAD_EDITS))
def test_bad_edit_never_reaches_the_pinned_config(pinned_copy, model_id):
    before = pinned_copy.read_text(encoding="utf-8")
    after = _edit(pinned_copy, [model_id]).read_text(encoding="utf-8")

    # The edit is real: the row's placement keys changed, nothing else did.
    assert _placement(after)[model_id] != _placement(before)[model_id]
    edited = yaml.safe_load(after)["models"]
    for mid, row in yaml.safe_load(before)["models"].items():
        strip = lambda r: {k: v for k, v in r.items() if k not in PLACEMENT_KEYS}  # noqa: E731
        assert strip(edited[mid]) == strip(row), mid

    # Pinned, the edit is invisible.
    assert yaml.safe_load(pin_placement_text(after)) == yaml.safe_load(pin_placement_text(before))


def test_pinned_text_keeps_every_comment(pinned_copy):
    """Pinning drops no comment the edited file still carries. (The editor
    itself can drop the comment block trailing a key it deletes.)"""
    after = _edit(pinned_copy, sorted(BAD_EDITS)).read_text(encoding="utf-8")
    comments = [line for line in after.splitlines() if line.lstrip().startswith("#")]
    pinned_lines = set(pin_placement_text(after).splitlines())
    assert [c for c in comments if c not in pinned_lines] == []


def test_table_row_missing_from_config_fails_loudly(pinned_copy):
    data = yaml.safe_load(pinned_copy.read_text(encoding="utf-8"))
    del data["models"]["piper"]

    with pytest.raises(AssertionError, match="piper"):
        pin_placement_text(yaml.safe_dump(data, sort_keys=False))


def test_affected_modules_stay_green_against_an_edited_config(pinned_copy, monkeypatch):
    """Every bad edit at once, then the modules such edits used to redden, in
    a fresh pytest process whose committed config is the edited copy."""
    edited = _edit(pinned_copy, sorted(BAD_EDITS))
    monkeypatch.setenv(COMMITTED_CONFIG_ENV, str(edited))
    assert committed_config_path() == edited
    env = {**os.environ, "PYTHONUTF8": "1"}
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *AFFECTED_MODULES],
        cwd=host_profile.PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
