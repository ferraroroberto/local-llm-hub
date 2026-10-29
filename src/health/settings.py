"""Health-watch thresholds and cadence, from ``config/health_watch.json``.

Re-read on every sample, so retuning a threshold needs no hub restart. The
file is merged over :data:`DEFAULTS` one section deep, so a partial file only
overrides what it names.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

from ..atomic_write import read_json

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = PROJECT_ROOT / "config" / "health_watch.json"

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "interval_s": 600,
    "boot_delay_s": 15,
    "history_hours": 24,
    "stale_after_intervals": 3,
    "top_n": 15,
    "ignore_processes": ["System Idle Process"],
    "reboot_only_processes": [
        "System", "Registry", "Memory Compression", "smss.exe", "csrss.exe",
        "wininit.exe", "services.exe", "lsass.exe",
    ],
    "attribution_share": 0.5,
    "leak": {
        "window_hours": 6,
        "min_samples": 4,
        "min_span_hours": 0.5,
        "min_rising_fraction": 0.6,
    },
    "process": {
        "handles": {"watch_value": 300000, "act_value": 1000000,
                    "watch_rate_per_hour": 5000, "act_rate_per_hour": 30000, "rate_min_value": 20000},
        "memory_mb": {"watch_value": None, "act_value": None,
                      "watch_rate_per_hour": 300, "act_rate_per_hour": 2000, "rate_min_value": 1000},
        "gdi": {"watch_value": 5000, "act_value": 9000,
                "watch_rate_per_hour": 200, "act_rate_per_hour": 1000, "rate_min_value": 1000},
        "user": {"watch_value": 5000, "act_value": 9000,
                 "watch_rate_per_hour": 200, "act_rate_per_hour": 1000, "rate_min_value": 1000},
        "threads": {"watch_value": 2000, "act_value": 5000,
                    "watch_rate_per_hour": 100, "act_rate_per_hour": 500, "rate_min_value": 300},
    },
    "system": {
        "commit_percent": {"watch": 85, "act": 95},
        "ram_available_percent": {"watch": 10, "act": 5, "below": True},
        "nonpaged_pool_percent": {"watch": 6, "act": 10},
        "paged_pool_percent": {"watch": 15, "act": 25},
        "total_handles": {"watch": 2000000, "act": 4000000},
        "tcp_time_wait": {"watch": 4000, "act": 10000},
        "ephemeral_ports_percent": {"watch": 50, "act": 80},
        "disk_free_percent": {"watch": 15, "act": 5, "below": True},
    },
}


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    """The effective config: the file (when readable) merged over the defaults."""
    merged = copy.deepcopy(DEFAULTS)
    data = read_json(path or DEFAULT_PATH, "health watch config")
    if not isinstance(data, dict):
        return merged
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            for sub, sub_value in value.items():
                if sub.startswith("_"):
                    continue
                if isinstance(sub_value, dict) and isinstance(merged[key].get(sub), dict):
                    merged[key][sub].update(sub_value)
                else:
                    merged[key][sub] = sub_value
        else:
            merged[key] = value
    return merged
