"""Load models.yaml and expose typed entries filtered by the active host.

One source of truth for: which models exist, how to launch them, which
port they listen on, and how the hub should route by model-name alias.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .host_profile import CONFIG_PATH, HostProfile, resolve as resolve_host


@dataclass(frozen=True)
class Model:
    id: str                         # short key in YAML ("qwen", "glm", "claude")
    display_name: str               # name the client sends in the `model` field
    backend: str                    # "claude" | "openai" | "gemini" | "whisper" | "tts"
    aliases: List[str] = field(default_factory=list)
    engine: Optional[str] = None
    # For backend == "tts" (engine "tts-server"): which synthesis engine
    # the shim loads — "chatterbox" (in-process torch) or "orpheus"
    # (llama-server GGUF child + SNAC decode). See src/tts_engines.py.
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


def _load_config() -> Dict:
    return yaml.safe_load(Path(CONFIG_PATH).read_text(encoding="utf-8")) or {}


def _row_to_model(model_id: str, row: Dict) -> Model:
    return Model(
        id=model_id,
        display_name=str(row.get("display_name") or model_id),
        backend=str(row.get("backend", "openai")),
        aliases=list(row.get("aliases", []) or []),
        engine=row.get("engine"),
        tts_engine=row.get("tts_engine"),
        port=int(row["port"]) if row.get("port") is not None else None,
        hf_repo=row.get("hf_repo"),
        hf_pattern=row.get("hf_pattern"),
        model_path=row.get("model_path"),
        args=list(row.get("args", []) or []),
        internal_port=int(row["internal_port"]) if row.get("internal_port") is not None else None,
        idle_seconds=int(row["idle_seconds"]) if row.get("idle_seconds") is not None else None,
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
