"""Role → model mapping API (issue #373, broadened by its follow-up comment).

Exposes ``config/models.yaml``'s ``roles:`` section — previously read only
server-side by ``src.model_registry.audio_role_chain()`` — as a single, flat,
general-purpose representation. Built as shared infrastructure rather than a
card-specific shim: issue #342 (dynamic model fallback) will read the same
role→model chain data to reason about failover, not just display it, so the
shape here is a clean role_key → {model_id, display_name, notes, fallback}
map, not tailored to what the Hub tab's card happens to render.

  * ``GET /admin/api/roles`` — every configured role, agentic + audio, keyed
    uniformly (audio sub-roles dotted: ``audio.transcribe`` etc.) so a caller
    never needs to know the YAML's nesting shape to iterate every role.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from src.host_profile import _load_config
from src.model_registry import all_models

router = APIRouter()


def _display_names() -> Dict[str, str]:
    return {m.id: m.display_name for m in all_models()}


def _role_entry(row: Dict[str, Any], names: Dict[str, str]) -> Dict[str, Any]:
    model_id: Optional[str] = row.get("model_id")
    fallback: List[str] = [str(x) for x in (row.get("fallback") or []) if x]
    return {
        "model_id": model_id,
        "display_name": names.get(model_id, model_id) if model_id else None,
        "notes": row.get("notes"),
        "fallback": fallback,
    }


def _collect_roles(roles_cfg: Dict[str, Any], names: Dict[str, str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, row in roles_cfg.items():
        if not isinstance(row, dict):
            continue
        if key == "audio":
            # Audio is one level deeper (transcribe/translate/speech) — flatten
            # with a dotted key so every role reads through the same shape.
            for sub_key, sub_row in row.items():
                if isinstance(sub_row, dict):
                    result[f"audio.{sub_key}"] = _role_entry(sub_row, names)
            continue
        result[key] = _role_entry(row, names)
    return result


@router.get("/api/roles")
async def get_roles() -> Dict[str, Any]:
    """Every configured role → ``{model_id, display_name, notes, fallback}``.

    Reads ``config/models.yaml`` through ``host_profile._load_config()`` — the
    same cached loader ``model_registry.audio_role_chain()`` uses — so this
    never re-parses the YAML independently.
    """
    cfg = _load_config()
    roles_cfg: Dict[str, Any] = cfg.get("roles") or {}
    names = _display_names()
    return {"roles": _collect_roles(roles_cfg, names)}
