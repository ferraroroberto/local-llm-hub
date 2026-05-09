"""Tray-specific config loaded from the ``tray:`` section of ``models.yaml``.

Kept intentionally small — only the knobs that change tray behaviour at
launch (autostart toggles + the model ids to bring up automatically). All
other behaviour comes from the existing registry / process modules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

import yaml

from src.host_profile import CONFIG_PATH
from src.model_registry import enabled_models

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrayConfig:
    autostart_hub: bool = True
    autostart_models: Tuple[str, ...] = field(default_factory=tuple)
    hub_ready_timeout_s: float = 30.0


def load() -> TrayConfig:
    """Read ``config/models.yaml`` and return a :class:`TrayConfig`.

    Missing ``tray:`` section → defaults (hub autostart on, no model
    autostart). Any ``autostart_models`` entry that isn't in the active
    host's ``enabled`` list is dropped — we log a warning so the user
    can spot the typo, but the tray still launches with the remaining
    valid ids.
    """
    raw_path = Path(CONFIG_PATH)
    try:
        cfg = yaml.safe_load(raw_path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        logger.warning("⚠️  could not read %s: %s — using tray defaults", raw_path, exc)
        return TrayConfig()

    section = cfg.get("tray") or {}
    autostart_hub = bool(section.get("autostart_hub", True))
    hub_ready_timeout = float(section.get("hub_ready_timeout_s", 30.0))

    raw_models = section.get("autostart_models")
    if raw_models is None:
        candidates: list[str] = []
    elif isinstance(raw_models, list):
        candidates = [str(m) for m in raw_models if m]
    else:
        logger.warning(
            "⚠️  tray.autostart_models must be a list (got %r) — skipping model autostart",
            raw_models,
        )
        candidates = []

    valid_ids = {m.id for m in enabled_models() if m.backend in ("openai", "whisper")}
    autostart_models: list[str] = []
    for model_id in candidates:
        if model_id in valid_ids:
            autostart_models.append(model_id)
        else:
            logger.warning(
                "⚠️  tray.autostart_models entry %r is not enabled on this host "
                "(enabled local models: %s) — skipping",
                model_id, sorted(valid_ids),
            )

    return TrayConfig(
        autostart_hub=autostart_hub,
        autostart_models=tuple(autostart_models),
        hub_ready_timeout_s=hub_ready_timeout,
    )
