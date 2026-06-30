"""Load models.yaml and expose typed entries filtered by the active host.

One source of truth for: which models exist, how to launch them, which
port they listen on, and how the hub should route by model-name alias.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .host_profile import CONFIG_PATH, HostProfile, resolve as resolve_host


@dataclass(frozen=True)
class Model:
    id: str                         # short key in YAML ("qwen", "glm", "claude")
    display_name: str               # name the client sends in the `model` field
    backend: str                    # "claude" | "openai" | "gemini" | "whisper" | "tts"
    aliases: List[str] = field(default_factory=list)
    engine: Optional[str] = None
    # For backend == "gemini": when true this row is an image-*generation*
    # model (agy's built-in Imagen tool), routed through
    # POST /v1/images/generations rather than the text chat paths. There is
    # no picker entry for it — the image tool is hosted inside an ordinary
    # Gemini text session (see src/gemini_cli._IMAGE_HOST_MODEL).
    image_gen: bool = False
    # For backend == "tts" (engine "tts-server"): which synthesis engine
    # the shim loads — chatterbox, kokoro, orpheus, piper. See src/tts_engines.py.
    tts_engine: Optional[str] = None
    port: Optional[int] = None
    hf_repo: Optional[str] = None
    hf_pattern: Optional[str] = None
    model_path: Optional[str] = None
    args: List[str] = field(default_factory=list)
    # Lazy-loaded engines (e.g. whisper-server-lazy) wrap the real
    # backend in a proxy that owns the child process. ``port`` stays the
    # external contract; ``internal_port`` is where the wrapped child
    # actually binds (loopback only). ``idle_seconds`` is how long the
    # child stays loaded after the last request before it's torn down.
    internal_port: Optional[int] = None
    idle_seconds: Optional[int] = None
    # A *virtual* model is an alias of another backend: it shares an existing
    # backend's ``port`` (so ``url`` already points at the running process) and
    # has no engine / weights of its own. It is never launched, downloaded, or
    # offered as a controllable process in the admin UI — it only exists to
    # route the chat shape with an ``inject_extra`` overlay. See the
    # ``qwen35_4b_nothink`` row in config/models.yaml.
    virtual: bool = False
    # Server-side defaults folded into the upstream OpenAI ``extra`` payload on
    # every /v1/chat/completions for this id (caller-sent fields win). Used by
    # the no-think alias to deliver ``chat_template_kwargs={enable_thinking:
    # false}`` to clients that can't send it themselves (e.g. Home Assistant's
    # extended_openai_conversation).
    inject_extra: Optional[Dict[str, Any]] = None

    @property
    def all_names(self) -> List[str]:
        # The registry ``id`` is the canonical handle used by tools that
        # walk the YAML (the SPA Playground dropdown, swap-model, etc.).
        # Include it so /v1/models lists every name a client can send.
        names = [self.id, self.display_name, *self.aliases]
        return [n for n in dict.fromkeys(names) if n]

    @property
    def url(self) -> Optional[str]:
        return f"http://127.0.0.1:{self.port}/v1" if self.port else None


# Parsed-YAML cache, keyed by the resolved config path. ``all_models()`` is
# called by ``enabled_models()`` / ``resolve()`` / ``known_names()`` — often
# several times per request — and each call used to re-parse the YAML. Keying
# on the path means swapping ``CONFIG_PATH`` (as the tests do) busts the cache
# transparently; ``reload()`` is the explicit escape hatch.
_CONFIG_CACHE: Dict[str, Dict] = {}


def _load_config() -> Dict:
    key = str(CONFIG_PATH)
    cached = _CONFIG_CACHE.get(key)
    if cached is not None:
        return cached
    data = yaml.safe_load(Path(CONFIG_PATH).read_text(encoding="utf-8")) or {}
    _CONFIG_CACHE[key] = data
    return data


def reload() -> None:
    """Drop the parsed-YAML cache (call after editing models.yaml in-process)."""
    _CONFIG_CACHE.clear()


def _row_to_model(model_id: str, row: Dict) -> Model:
    return Model(
        id=model_id,
        display_name=str(row.get("display_name") or model_id),
        backend=str(row.get("backend", "openai")),
        aliases=list(row.get("aliases", []) or []),
        engine=row.get("engine"),
        image_gen=bool(row.get("image_gen", False)),
        tts_engine=row.get("tts_engine"),
        port=int(row["port"]) if row.get("port") is not None else None,
        hf_repo=row.get("hf_repo"),
        hf_pattern=row.get("hf_pattern"),
        model_path=row.get("model_path"),
        args=list(row.get("args", []) or []),
        internal_port=int(row["internal_port"]) if row.get("internal_port") is not None else None,
        idle_seconds=int(row["idle_seconds"]) if row.get("idle_seconds") is not None else None,
        virtual=bool(row.get("virtual", False)),
        inject_extra=row.get("inject_extra") or None,
    )


def all_models() -> List[Model]:
    cfg = _load_config()
    rows: Dict = cfg.get("models") or {}
    return [_row_to_model(mid, row) for mid, row in rows.items()]


def enabled_models(host: Optional[HostProfile] = None) -> List[Model]:
    """Return models the active host is configured to serve.

    Claude and Gemini are always enabled (subscription/CLI paths don't
    cost disk or VRAM) — openai-backed local models must appear in the
    host's `enabled` list.
    """
    profile = host or resolve_host()
    whitelist = set(profile.enabled)
    result: List[Model] = []
    for m in all_models():
        if m.backend in ("claude", "gemini") or m.id in whitelist:
            result.append(m)
    return result


def autostart_model_ids(host: Optional[HostProfile] = None) -> List[str]:
    """Configured local backend ids to start with the hub.

    The YAML list is user-editable, so filter it through the active host and
    launchable backend rows. Virtual aliases share a real backend and never
    own a process, so they are excluded even if listed by mistake.
    """
    cfg = _load_config()
    raw = (cfg.get("tray") or {}).get("autostart_models") or []
    if not isinstance(raw, list):
        return []
    valid = {
        m.id for m in enabled_models(host)
        if m.backend in ("openai", "whisper", "tts") and not m.virtual
    }
    return [model_id for model_id in (str(item) for item in raw if item) if model_id in valid]

def resolve(name: str, host: Optional[HostProfile] = None) -> Optional[Model]:
    """Look up a model by any of its names — registry id, display_name, or alias.

    Accepting ``id`` matters for tools that drive the hub off the YAML
    directly: the SPA Playground dropdown sends ``m.id`` (the YAML key),
    swap-model references ids when rewiring roles, and the hub's own
    ``run_backend`` picks the entry by id. Without this, every model
    whose id is not also listed in ``aliases`` 400'd on the Playground.
    """
    name = name.strip()
    for m in enabled_models(host):
        if name == m.id or name == m.display_name or name in m.aliases:
            return m
    return None


def known_names(host: Optional[HostProfile] = None) -> List[str]:
    names: List[str] = []
    for m in enabled_models(host):
        names.extend(m.all_names)
    return sorted(dict.fromkeys(names))

