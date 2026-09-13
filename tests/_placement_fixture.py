"""The fleet placement the unit tests reason about (#564, #565).

Pinned rather than read from ``config/models.yaml``: the admin UI's placement
card (#424) rewrites a row's ``host:`` / ``hosts:``, ``startup:`` and
``idle_unload_minutes:`` and commits straight to ``main`` without running the
suite. #561 pinned whisper's chain after ``4aefa09`` reordered it and turned
five tests red on an untouched main; #564 pinned every chain; #565 pins the
two lifecycle keys too, so no edit that write path can make reaches a test.

``tests/conftest.py`` points ``host_profile.CONFIG_PATH`` at a copy of the
committed config run through :func:`pin_placement_text` before any test module
imports ``src``, so every test reads the pinned config by default.

Only those four keys are pinned. Everything else stays the committed config —
host inventory, ``enabled:`` lists, VRAM estimates and ceilings, comments — so
the tests still drive the real parser, placement derivations and YAML editor.
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List

# Every key ``src.config_write.edit_models_yaml`` may add, change or remove on
# a ``models:`` row. tests/test_placement_fixture.py proves each one is pinned.
PLACEMENT_KEYS = ("host", "hosts", "startup", "idle_unload_minutes")

# Overrides the committed config the pinned copy is built from — only so
# tests/test_placement_fixture.py can rerun the suite against an edited copy.
COMMITTED_CONFIG_ENV = "LOCAL_LLM_HUB_TEST_COMMITTED_CONFIG"

# whisper's failover chain: a GPU-preferred head (gaming), a warm middle link
# (mac-mini-m4) and a degraded CPU last resort (tower).
WHISPER_FIXTURE_CHAIN: List[Any] = ["gaming", "mac-mini-m4", {"id": "tower", "cpu": True}]

# The placement keys of every row, spelled as they appear in the YAML (a bare
# ``host:`` stays bare, so the editor tests keep a ``host:`` row to convert).
# A row absent from this table carries none of the keys: unowned (local
# everywhere) and eager. Each chain host must enable its row in the committed
# ``enabled:`` lists, or the row has no eligible owner.
PLACEMENT_FIXTURE_ROWS: Dict[str, Dict[str, Any]] = {
    "flux1_local": {"host": "tower", "startup": "on_demand", "idle_unload_minutes": 15},
    "flux2_klein": {"host": "tower", "startup": "on_demand", "idle_unload_minutes": 15},
    "qwen": {"host": "mac-mini-m4"},
    "glm": {"startup": "on_demand"},
    "qwen35_4b": {"host": "tower"},
    "qwen35_4b_nothink": {"host": "tower"},
    "gemma4_e4b": {"host": "tower", "startup": "on_demand", "idle_unload_minutes": 30},
    "gemma4_26b": {"host": "tower", "startup": "on_demand", "idle_unload_minutes": 30},
    "whisper": {"hosts": WHISPER_FIXTURE_CHAIN},
    "whisper_translate": {"host": "gaming"},
    "whisper_vanilla": {"host": "gaming", "startup": "on_demand", "idle_unload_minutes": 5},
    "parakeet": {"host": "mac-mini-m4"},
    "chatterbox": {"host": "tower", "startup": "on_demand", "idle_unload_minutes": 30},
    "piper": {"host": "tower"},
    "orpheus": {"hosts": ["tower", "gaming"]},
    "kokoro": {"host": "tower", "startup": "on_demand", "idle_unload_minutes": 30},
}

# ``desired_placement()`` under the table above: each eager row on its first
# enabling chain host, in config row order. Written out by hand, not computed
# from the table, so a changed table fails the tests instead of silently
# moving the expectation along with it.
FIXTURE_DESIRED_PLACEMENT: Dict[str, List[str]] = {
    "tower": ["qwen35_4b", "piper", "orpheus"],
    "mac-mini-m4": ["qwen", "parakeet"],
    "gaming": ["whisper", "whisper_translate"],
}


def committed_config_path() -> Path:
    """The committed ``config/models.yaml``, or the ``COMMITTED_CONFIG_ENV``
    override when set."""
    override = os.environ.get(COMMITTED_CONFIG_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "config" / "models.yaml"


def _yaml():
    """ruamel round-trip settings matching ``src.config_write.edit_models_yaml``,
    so an unchanged file survives the pin byte for byte."""
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 2 ** 16
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _flow(value: Any) -> Any:
    """A chain as the file writes it: one flow-style line."""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    if not isinstance(value, list):
        return value
    seq = CommentedSeq()
    for entry in value:
        if isinstance(entry, dict):
            entry = CommentedMap(entry)
            entry.fa.set_flow_style()
        seq.append(entry)
    seq.fa.set_flow_style()
    return seq


def _pin_row(row: Any, pinned: Dict[str, Any]) -> bool:
    """Set ``row``'s placement keys to exactly ``pinned``, in place, touching
    only keys whose value differs. Returns True when the row changed."""
    changed = False
    for key in PLACEMENT_KEYS:
        if key not in pinned:
            continue
        if key in row:
            if row[key] != pinned[key]:
                row[key] = _flow(pinned[key])
                changed = True
            continue
        keys = list(row.keys())
        # A chain key takes the other chain key's slot; the lifecycle keys
        # follow the key before them, as edit_models_yaml inserts them.
        anchors = {
            "host": ["hosts"],
            "hosts": ["host"],
            "startup": ["hosts", "host", "port"],
            "idle_unload_minutes": ["startup"],
        }[key]
        pos = len(keys)
        for anchor in anchors:
            if anchor in keys:
                pos = keys.index(anchor) + (0 if key in ("host", "hosts") else 1)
                break
        row.insert(pos, key, _flow(pinned[key]))
        changed = True
    for key in PLACEMENT_KEYS:
        if key in row and key not in pinned:
            del row[key]
            changed = True
    return changed


def pin_placement_text(text: str) -> str:
    """``models.yaml`` text with every row's placement keys replaced by its
    ``PLACEMENT_FIXTURE_ROWS`` entry, comments preserved. Text that already
    matches the table comes back unchanged.

    A table row the config no longer has fails loudly: a silently ignored
    entry would leave the tests asserting a placement nothing produces.
    """
    yaml = _yaml()
    data = yaml.load(text)
    models = data["models"]
    stale = sorted(set(PLACEMENT_FIXTURE_ROWS) - set(models))
    if stale:
        raise AssertionError(
            f"PLACEMENT_FIXTURE_ROWS pins rows config/models.yaml no longer has: {stale}"
        )
    changed = False
    for model_id, row in models.items():
        changed |= _pin_row(row, PLACEMENT_FIXTURE_ROWS.get(model_id, {}))
    if not changed:
        return text
    buf = StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()
