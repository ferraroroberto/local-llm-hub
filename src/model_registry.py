"""Load models.yaml and expose typed entries filtered by the active host.

One source of truth for: which models exist, how to launch them, which
port they listen on, and how the hub should route by model-name alias.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .host_profile import HostProfile, _load_config, resolve as resolve_host


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
    # the shim loads — chatterbox, kokoro, orpheus, piper. See src/tts_engines/.
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
    # Which host profile *owns* (spawns/manages) this model's process. Unset
    # means "whichever host resolves this row locally" (the pre-#178
    # behavior — every model was implicitly local). When set and it differs
    # from the active host, this row is a *remote* model: the active host
    # never spawns/health-checks it locally and instead proxies requests to
    # the owning host's own hub. See src/remote_proxy.py.
    host: Optional[str] = None
    # Rough static GPU-VRAM footprint in MB (#375) — enough for the fleet
    # placement grid to flag a host overcommit (sum of a host's placed models
    # vs its ``HostProfile.vram_mb`` ceiling), NOT live telemetry. CPU-only /
    # off-GPU / virtual rows are 0; subscription rows leave it None. A None
    # value contributes 0 to the sum. See config/models.yaml for the estimates.
    est_vram_mb: Optional[int] = None

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


# ``_load_config()`` (imported above) is host_profile's cached YAML loader
# for config/models.yaml — both modules read the same file, so there is one
# cache rather than two kept in sync by convention.


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
        host=row.get("host"),
        est_vram_mb=int(row["est_vram_mb"]) if row.get("est_vram_mb") is not None else None,
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


def local_models(host: Optional[HostProfile] = None) -> List[Model]:
    """``enabled_models()`` filtered to rows this host actually runs —
    excludes any row owned by a *different* host (``m.host`` set and not
    this one). The generalization of "give me the models I might spawn,
    manage, download weights for, or health-check" — every place a
    remote-owned row (cross-enabled so it *resolves* here, but proxied
    rather than run here) would otherwise be mistaken for a local one:
    install checks, port-liveness checks, spawn/inherit loops, tray menus.
    """
    profile = host or resolve_host()
    return [m for m in enabled_models(host) if not (m.host and m.host != profile.id)]


# Backends that own a spawnable local process. `claude` / `gemini` are
# subscription/CLI paths with nothing to launch; a `virtual` row shares
# another model's process. Everything a `run_backend <id>` can actually
# start is one of these three engines and non-virtual.
_SPAWNABLE_BACKENDS = ("openai", "whisper", "tts")


def launchable_local_ids(host: Optional[HostProfile] = None) -> List[str]:
    """Ids of models this host can actually spawn as its own local process.

    ``local_models()`` (enabled ∧ owned-here) narrowed to rows with a
    spawnable backend, dropping virtual aliases (which share another row's
    process). This is exactly the set ``run_backend <id>`` starts without
    erroring — the single source the bulk launchers enumerate so they can
    never drift from the active host's ``enabled:`` contract.
    """
    return [
        m.id for m in local_models(host)
        if m.backend in _SPAWNABLE_BACKENDS and not m.virtual
    ]


def autostart_model_ids(host: Optional[HostProfile] = None) -> List[str]:
    """Configured local backend ids to start with the hub.

    ``config/startup_profile.json`` (issue #265) is the source of truth —
    the admin UI's Startup card reads/writes it. The live file is gitignored
    (issue #304); ``load_startup_profile`` falls back to the committed
    ``startup_profile.example.json`` template, so the profile still drives a
    fresh clone. Only when *neither* the live file nor the example exists do we
    fall back to the legacy ``config/models.yaml`` → ``tray.autostart_models``
    list so a clean checkout still autostarts something sensible. Either way
    the raw id list is filtered through the active host and launchable
    backend rows: virtual aliases share a real backend and never own a
    process, so they are excluded even if listed by mistake, and rows owned
    by a *different* host (``m.host`` set and not this one) are remote —
    never autostarted locally, the owning host's own tray does that.
    """
    from src.startup_profile import (
        DEFAULT_PROFILE_PATH,
        EXAMPLE_PROFILE_PATH,
        load_startup_profile,
    )

    if DEFAULT_PROFILE_PATH.exists() or EXAMPLE_PROFILE_PATH.exists():
        raw: List[str] = load_startup_profile().models
    else:
        cfg = _load_config()
        legacy = (cfg.get("tray") or {}).get("autostart_models") or []
        raw = [str(item) for item in legacy if item] if isinstance(legacy, list) else []

    valid = set(launchable_local_ids(host))
    return [model_id for model_id in raw if model_id in valid]

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


def audio_role_chain(role_key: str) -> List[str]:
    """Ordered model-id chain for an audio role (issue #348).

    Reads ``roles.audio.<role_key>`` from models.yaml and returns
    ``[model_id, *fallback]`` — the primary followed by the ordered failover
    models the audio proxy tries in turn when the primary's backend is
    unavailable. ``role_key`` is ``"transcribe"`` / ``"translate"`` / ``"speech"``.

    A role with only ``model_id`` (no ``fallback``) yields a one-element chain —
    identical to the pre-#348 single-target behaviour. Returns ``[]`` when the
    role is not configured, so the caller can fall back to its own heuristic.
    Duplicate ids are collapsed (order-preserving) so a config that repeats the
    primary in ``fallback`` never makes the proxy retry the same dead backend.
    """
    cfg = _load_config()
    role = ((cfg.get("roles") or {}).get("audio") or {}).get(role_key)
    if not isinstance(role, dict):
        return []
    chain: List[str] = []
    primary = role.get("model_id")
    if primary:
        chain.append(str(primary))
    fallback = role.get("fallback")
    if isinstance(fallback, list):
        chain.extend(str(x) for x in fallback if x)
    seen: set[str] = set()
    return [mid for mid in chain if not (mid in seen or seen.add(mid))]

