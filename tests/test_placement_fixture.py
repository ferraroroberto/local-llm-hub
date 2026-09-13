"""tests/_placement_fixture.py actually decouples the suite from production
placement (#564).

The admin UI's placement card commits host-chain edits straight to ``main``
(#424). These tests move rows in a temp copy of the committed config, the
way such an edit would, and check two things: the move really lands in the
registry, and the pinned config derives exactly what the unmoved pinned
config does. Neither check reads a ``startup:`` mode or a committed chain, so
these tests add no coupling of their own to production config.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from src import host_profile, model_registry
from tests._placement_fixture import PLACEMENT_FIXTURE_CHAINS, pin_placement

# One admin-UI-style move per placed row: each to a different host that
# enables it in the committed config, or a reorder of a multi-host chain.
MOVES = {
    "qwen35_4b": ["mac-mini-m4"],
    "piper": ["mac-mini-m4"],
    "orpheus": ["gaming", "tower"],
    "qwen": ["tower"],
    "parakeet": ["tower"],
    "whisper_translate": ["tower"],
    "whisper": ["mac-mini-m4", "gaming"],
}


def _load(cfg, data: dict):
    """Write ``data`` as the active config; return ``(placement, chains)``."""
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    host_profile._CONFIG_CACHE.clear()
    chains = {m.id: m.hosts for m in model_registry.all_models(apply_cpu_offload=False)}
    return model_registry.desired_placement(), chains


@pytest.mark.parametrize("model_id", sorted(MOVES))
def test_moved_row_does_not_reach_pinned_placement(config_with_example_identity, model_id):
    cfg = config_with_example_identity
    committed = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    baseline, _ = _load(cfg, pin_placement(copy.deepcopy(committed)))

    moved = copy.deepcopy(committed)
    moved["models"][model_id].pop("host", None)
    moved["models"][model_id]["hosts"] = MOVES[model_id]

    # The move is real: unpinned, the registry serves the edited chain.
    _, chains = _load(cfg, copy.deepcopy(moved))
    assert chains[model_id] == MOVES[model_id]
    # Pinned, the edit is invisible: same chain as the table, same placement.
    placement, chains = _load(cfg, pin_placement(moved))
    table_hosts = [
        e["id"] if isinstance(e, dict) else e for e in PLACEMENT_FIXTURE_CHAINS[model_id]
    ]
    assert table_hosts != MOVES[model_id]
    assert chains[model_id] == table_hosts
    assert placement == baseline


def test_host_added_to_unowned_row_is_stripped(config_with_example_identity):
    """A row the table leaves unowned stays unowned, whatever the config says."""
    data = yaml.safe_load(config_with_example_identity.read_text(encoding="utf-8"))
    assert "glm" not in PLACEMENT_FIXTURE_CHAINS
    data["models"]["glm"]["host"] = "tower"

    assert "host" not in pin_placement(data)["models"]["glm"]


def test_table_row_missing_from_config_fails_loudly(config_with_example_identity):
    data = yaml.safe_load(config_with_example_identity.read_text(encoding="utf-8"))
    del data["models"]["piper"]

    with pytest.raises(AssertionError, match="piper"):
        pin_placement(data)
