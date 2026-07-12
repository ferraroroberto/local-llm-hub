"""The hub's declarative "what should be up at launch" profile (issue #265).

Single source of truth for what the hub brings up automatically on every
boot (tray, ``run_hub.bat``, or ``python -m src.run_backend hub``):

  * ``docker`` / ``langfuse`` — whether to run ``services.launch_stack()``
    (start Docker Desktop if down, then the Langfuse containers) at startup.
  * ``mac_mini_sync`` — whether to wake/sync the Mac Mini host at startup.
  * ``agentsview`` — whether to run ``services.launch_agentsview()`` (the
    optional AgentsView server feeding the Code tab's AGY vendor, #280).
  * ``models`` — local backend ids to autostart, superseding the legacy
    ``config/models.yaml`` → ``tray.autostart_models`` list (still read as a
    fallback by ``model_registry.autostart_model_ids()`` when this file is
    missing, e.g. on a fresh clone before the admin UI has saved a profile).

Committed (unlike ``config/webapp_config.json``, which is machine-local
auth) since this is ordinary, inspectable project config — same shelf as
``config/transcription_glossary.json``, whose load/save shape this mirrors
(atomic write, cache clear on save, tolerant load that never raises).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "config" / "startup_profile.json"


@dataclass(frozen=True)
class StartupProfile:
    docker: bool = True
    langfuse: bool = True
    mac_mini_sync: bool = True
    # AgentsView server for the Code tab's AGY vendor (issue #280) — launch
    # soft-fails with a log line when the tool isn't installed.
    agentsview: bool = True
    models: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


_DEFAULT = StartupProfile()

# Parsed cache keyed by the resolved profile path (same shape as
# host_profile._CONFIG_CACHE) — keying on the path rather than relying on
# an lru_cache'd optional arg means swapping DEFAULT_PROFILE_PATH (as tests
# do) transparently busts the cache instead of returning a stale hit.
_PROFILE_CACHE: Dict[str, StartupProfile] = {}


def load_startup_profile(path: Optional[str] = None) -> StartupProfile:
    """Load the startup profile. Missing/unparseable file → the defaults.

    A broken or absent profile must never prevent the hub from starting —
    same tolerant-load contract as ``transcription_glossary.load_rules()``.
    """
    target = Path(path) if path else DEFAULT_PROFILE_PATH
    key = str(target)
    cached = _PROFILE_CACHE.get(key)
    if cached is not None:
        return cached

    if not target.exists():
        result = _DEFAULT
    else:
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("⚠️ could not load startup profile %s: %s", target, exc)
            data = None
        if not isinstance(data, dict):
            result = _DEFAULT
        else:
            models = data.get("models")
            result = StartupProfile(
                docker=bool(data.get("docker", True)),
                langfuse=bool(data.get("langfuse", True)),
                mac_mini_sync=bool(data.get("mac_mini_sync", True)),
                agentsview=bool(data.get("agentsview", True)),
                models=[str(m) for m in models if m] if isinstance(models, list) else [],
            )

    _PROFILE_CACHE[key] = result
    return result


def normalize_profile(data: Dict[str, Any]) -> StartupProfile:
    """Validate + clean an incoming profile payload for persistence.

    ``models`` is filtered against ``model_registry.launchable_local_ids()``
    so the admin UI can never persist a stale/typo'd id that would silently
    no-op at startup — imported lazily to avoid a load-time import cycle
    (``model_registry.autostart_model_ids()`` reads this module back).
    """
    if not isinstance(data, dict):
        raise ValueError("startup profile must be a JSON object")
    raw_models = data.get("models", [])
    if not isinstance(raw_models, list):
        raise ValueError("'models' must be a list")

    from src.model_registry import launchable_local_ids

    valid_ids = set(launchable_local_ids())
    models = [m for m in (str(item) for item in raw_models if item) if m in valid_ids]

    return StartupProfile(
        docker=bool(data.get("docker", True)),
        langfuse=bool(data.get("langfuse", True)),
        mac_mini_sync=bool(data.get("mac_mini_sync", True)),
        agentsview=bool(data.get("agentsview", True)),
        models=models,
    )


def save_startup_profile(data: Dict[str, Any], path: Optional[str] = None) -> StartupProfile:
    """Validate, atomically write, and invalidate the load cache."""
    target = Path(path) if path else DEFAULT_PROFILE_PATH
    clean = normalize_profile(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(clean.as_dict(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    _PROFILE_CACHE.pop(str(target), None)
    logger.info(
        "💾 Saved startup profile (docker=%s langfuse=%s mac_mini_sync=%s "
        "agentsview=%s models=%s)",
        clean.docker, clean.langfuse, clean.mac_mini_sync,
        clean.agentsview, clean.models,
    )
    return clean
