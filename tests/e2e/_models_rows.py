"""Placement-row fixtures shared by the Models tab's placement e2e suites.

Each suite pins the ``/admin/api/models`` payload with route interception; these
two rows are the ones both render, so they live in one place. ``placement_extra``
adds keys to the row's ``placement`` (the editor suite sets ``editable``).
"""

from __future__ import annotations

from typing import Any


def whisper_row(**placement_extra: Any) -> dict[str, Any]:
    """Remote-owned chain with a degraded cpu last resort."""
    return {
        "id": "whisper", "display_name": "whisper-large-v3-turbo",
        "backend": "whisper", "engine": "whisper-server", "port": 8090,
        "url": None, "aliases": [], "controllable": True,
        "ownership": "ours", "pid": 777, "reachable": True,
        "model_path": None, "host": "gaming",
        "preferred_host": "gaming", "failover": False,
        "placement": {
            "chain": [
                {"id": "gaming", "cpu": False},
                {"id": "mac-mini-m4", "cpu": False},
                {"id": "tower", "cpu": True},
            ],
            "startup": "eager", "idle_unload_minutes": None,
            "est_vram_mb": 2000, **placement_extra,
        },
    }


def gemma_row(**placement_extra: Any) -> dict[str, Any]:
    """On-demand llama-server row, idle-unloaded (stopped, waits for a request)."""
    return {
        "id": "gemma4_26b", "display_name": "gemma4-26b-a4b-it",
        "backend": "openai", "engine": "llama-server", "port": 8087,
        "url": None, "aliases": ["agentic_heavy"], "controllable": True,
        "ownership": "none", "pid": None, "reachable": False,
        "model_path": None, "host": "tower",
        "placement": {
            "chain": [{"id": "tower", "cpu": False}],
            "startup": "on_demand", "idle_unload_minutes": 30,
            "est_vram_mb": 13400, **placement_extra,
        },
    }
