"""The fleet placement the unit tests reason about (#564).

Pinned rather than read from ``config/models.yaml``: every row's host
declaration is editable from the admin UI's placement card (#424), whose
write-through commits straight to ``main`` without running the suite. #561
pinned whisper's chain after ``4aefa09`` reordered it and turned five tests
red on an untouched main; any other placed row could do the same. So the
tests assert against this table, never against today's production placement.

Only host declarations are pinned. Everything else stays the committed
config — host inventory, ``enabled:`` lists, VRAM estimates and ceilings,
``startup:`` modes — so the tests still drive the real chain parser and
placement derivations.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List

# whisper's failover chain: a GPU-preferred head (gaming), a warm middle link
# (mac-mini-m4) and a degraded CPU last resort (tower).
WHISPER_FIXTURE_CHAIN: List[Any] = ["gaming", "mac-mini-m4", {"id": "tower", "cpu": True}]

# The ``hosts:`` chain of every placed row. A row absent from this table is
# unowned under the fixture (local everywhere), so a host an admin-UI edit adds
# to a committed row never reaches a test either. Each chain host must enable
# its row in the committed ``enabled:`` lists, or the row has no eligible owner.
PLACEMENT_FIXTURE_CHAINS: Dict[str, List[Any]] = {
    "flux1_local": ["tower"],
    "flux2_klein": ["tower"],
    "qwen": ["mac-mini-m4"],
    "qwen35_4b": ["tower"],
    "qwen35_4b_nothink": ["tower"],
    "gemma4_e4b": ["tower"],
    "gemma4_26b": ["tower"],
    "whisper": WHISPER_FIXTURE_CHAIN,
    "whisper_translate": ["gaming"],
    "whisper_vanilla": ["gaming"],
    "parakeet": ["mac-mini-m4"],
    "chatterbox": ["tower"],
    "piper": ["tower"],
    "orpheus": ["tower", "gaming"],
    "kokoro": ["tower"],
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


def pin_placement(config: Dict[str, Any]) -> Dict[str, Any]:
    """Replace every row's ``host:`` / ``hosts:`` in a parsed ``models.yaml``
    with its ``PLACEMENT_FIXTURE_CHAINS`` entry, in place, and return it.

    A table row the config no longer has fails loudly: a silently ignored
    entry would leave the tests asserting a placement nothing produces.
    """
    models = config["models"]
    stale = sorted(set(PLACEMENT_FIXTURE_CHAINS) - set(models))
    if stale:
        raise AssertionError(
            f"PLACEMENT_FIXTURE_CHAINS pins rows config/models.yaml no longer has: {stale}"
        )
    for model_id, row in models.items():
        row.pop("host", None)
        row.pop("hosts", None)
        chain = PLACEMENT_FIXTURE_CHAINS.get(model_id)
        if chain is not None:
            row["hosts"] = copy.deepcopy(chain)
    return config
