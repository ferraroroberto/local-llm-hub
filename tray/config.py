"""Tray-specific config loaded from the ``tray:`` section of ``models.yaml``.

Kept intentionally small — only the knobs that change tray behaviour at
launch (autostart toggles + the model id to bring up automatically). All
other behaviour comes from the existing registry / process modules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from src.host_profile import CONFIG_PATH
from src.model_registry import enabled_models

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrayConfig:
    autostart_hub: bool = True
    autostart_model: Optional[str] = None
    hub_ready_timeout_s: float = 30.0


def load() -> TrayConfig:
    """Read ``config/models.yaml`` and return a :class:`TrayConfig`.

    Missing ``tray:`` section → defaults (hub autostart on, no model
    autostart). An ``autostart_model`` that isn't in the active host's
    ``enabled`` list is treated as missing — we log a warning so the user
    can spot the typo, but the tray still launches.
    """
    raw_path = Path(CONFIG_PATH)
    try:
        cfg = yaml.safe_load(raw_path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        logger.warning("⚠️  could not read %s: %s — using tray defaults", raw_path, exc)
        return TrayConfig()

    section = cfg.get("tray") or {}
    autostart_hub = bool(section.get("autostart_hub", True))
    autostart_model = section.get("autostart_model") or None
    hub_ready_timeout = float(section.get("hub_ready_timeout_s", 30.0))

    if autostart_model:
        valid_ids = {m.id for m in enabled_models() if m.backend in ("openai", "whisper")}
        if autostart_model not in valid_ids:
            logger.warning(
                "⚠️  tray.autostart_model=%r is not enabled on this host "
                "(enabled local models: %s) — skipping model autostart",
                autostart_model, sorted(valid_ids),
            )
            autostart_model = None

    return TrayConfig(
        autostart_hub=autostart_hub,
        autostart_model=autostart_model,
        hub_ready_timeout_s=hub_ready_timeout,
    )
