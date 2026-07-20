"""Diagnostics settings — retention + the opt-in scheduled snapshot (#315).

A tiny gitignored JSON file beside the startup profile, with the same tolerant
load / atomic save contract: a broken settings file must never stop a capture
or the hub. Kept separate from ``startup_profile.json`` because that file is
"what to launch at boot" and this one is "how the diagnostics feature
behaves" — merging them would make the Startup card and the diagnostics modal
write the same document from two places.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "diagnostics_settings.json"

MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650
MIN_SCHEDULE_HOURS = 1.0
MAX_SCHEDULE_HOURS = 24.0 * 14


@dataclass(frozen=True)
class DiagnosticsSettings:
    retention_days: int = 90
    # Scheduled snapshots are OFF by default: the feature's whole premise is
    # that it costs nothing until asked for.
    scheduled_enabled: bool = False
    scheduled_interval_hours: float = 24.0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


_DEFAULT = DiagnosticsSettings()
_cache: Dict[str, DiagnosticsSettings] = {}
_path_override: Optional[Path] = None


def set_settings_path(path: Optional[Path]) -> None:
    global _path_override
    _path_override = Path(path) if path else None
    _cache.clear()


def _target() -> Path:
    return _path_override or DEFAULT_SETTINGS_PATH


def load_settings() -> DiagnosticsSettings:
    """Load settings; a missing or unparseable file yields the defaults."""
    target = _target()
    key = str(target)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    result = _DEFAULT
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("⚠️ could not load diagnostics settings: %s", exc)
            data = None
        if isinstance(data, dict):
            result = normalize(data)
    _cache[key] = result
    return result


def normalize(data: Dict[str, Any]) -> DiagnosticsSettings:
    """Clamp an incoming payload into a valid settings object."""
    if not isinstance(data, dict):
        raise ValueError("diagnostics settings must be a JSON object")
    try:
        retention = int(data.get("retention_days", _DEFAULT.retention_days))
    except (TypeError, ValueError):
        retention = _DEFAULT.retention_days
    try:
        hours = float(data.get("scheduled_interval_hours", _DEFAULT.scheduled_interval_hours))
    except (TypeError, ValueError):
        hours = _DEFAULT.scheduled_interval_hours
    return DiagnosticsSettings(
        retention_days=max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, retention)),
        scheduled_enabled=bool(data.get("scheduled_enabled", _DEFAULT.scheduled_enabled)),
        scheduled_interval_hours=max(MIN_SCHEDULE_HOURS, min(MAX_SCHEDULE_HOURS, hours)),
    )


def save_settings(data: Dict[str, Any]) -> DiagnosticsSettings:
    """Validate, atomically write, invalidate the cache."""
    target = _target()
    clean = normalize(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(clean.as_dict(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    _cache.pop(str(target), None)
    logger.info(
        "💾 Saved diagnostics settings (retention=%dd scheduled=%s every %.0fh)",
        clean.retention_days, clean.scheduled_enabled, clean.scheduled_interval_hours,
    )
    return clean
