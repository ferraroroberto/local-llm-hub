"""Local multi-model hub: Anthropic-compatible and OpenAI-compatible endpoints.

Each request resolves its `model` field against `config/models.yaml`
(by registry id, display_name, or alias) and routes by the resolved
row's `backend`. The backend families dispatched on:

- claude   -> `claude -p` subprocess (Anthropic subscription)
- gemini   -> `agy` Antigravity CLI (Google AI Pro subscription)
- openai   -> a llama-server process on its own port (/v1)
- whisper  -> whisper-server / parakeet-server (/v1/audio/* ASR)
- tts      -> tts-server (/v1/audio/speech)
- comfyui  -> ComfyUI (/v1/images/*)

Which rows exist, which host owns each, and which port each listens on
are `config/models.yaml`'s alone — deliberately not restated here, since
a copy of the registry in this docstring is a copy that goes stale (#505).

Shapes exposed:
  * POST /v1/messages          - Anthropic shape (drop-in for the SDK)
  * POST /v1/messages/count_tokens
                               - Anthropic shape; exact on llama-server,
                                 explicitly flagged as approximate on the
                                 subscription-CLI backends
  * POST /v1/chat/completions  - OpenAI shape (passthrough/translation)
  * GET  /v1/models            - union of enabled names (both shapes)
  * POST /v1/audio/transcriptions, /v1/audio/translations
    GET  /v1/audio/health      - ASR proxy (`server_audio_asr.py`)
  * POST /v1/audio/speech      - TTS proxy (`server_audio_tts.py`)
  * POST /v1/images/generations, /v1/images/edits
                               - image gen/edit (`server_images.py`);
                                 edits are gemini-only
  * POST /v1/systemone         - TypeSafe Jev passthrough (`server_systemone.py`);
                                 the hub's only third-party egress, opt-in

Caveats: image and document content blocks work on the claude-* and gemini-* paths
(decoded to a per-request temp dir); local llama-server backends are
text-only and 400 on image input. No tool_use round-trip on the
Anthropic shape for non-claude backends (OpenAI-shape callers get tool
use natively from llama-server). Both chat routes stream SSE:
``/v1/chat/completions`` proxies upstream OpenAI events, while
``/v1/messages`` emits Anthropic events from Claude CLI or translated
llama-server deltas. Both scrub implicit reasoning from text output.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

# Load .env *before* importing anything that reads env at module-import
# time (observability.py reads OTEL_* / LANGFUSE_* immediately on
# init_otel()). Soft-fails when python-dotenv isn't installed — the
# hub still runs, just without auto-loading the project .env file.
try:
    from dotenv import load_dotenv as _load_dotenv

    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        _load_dotenv(_env_path, override=False)
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, Response

from .routes.anthropic_errors import install_anthropic_error_handlers
from .compression import StreamSafeGZipMiddleware
from .cors_policy import install_cors
from . import gemini_cli
from .host_profile import hub_bind_host, hub_port
from .hub_log import install_root_handler
from .hub_observability import ObservatoryMiddleware
from .model_registry import enabled_models
from .observability import init_otel, instrument_fastapi_app
from .routes.anthropic_messages import router as _anthropic_router
from .routes.openai_chat import router as _openai_router
from .audio.server_audio_asr import router as _audio_router
from .audio import server_audio_tts as _server_audio_tts  # noqa: F401 — registers /v1/audio/speech onto _audio_router
from .images.server_images import router as _images_router
from .routes.server_otel_receiver import router as _otel_receiver_router
from .routes.server_systemone import router as _systemone_router
from .trace_id_middleware import TraceIdHeaderMiddleware
from .webapp_config import WebappConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
# Wire the in-memory ring handler so the admin webapp's Hub tab can tail
# both our logs and uvicorn's (access + error) without re-reading stdout.
install_root_handler()
logger = logging.getLogger(__name__)

# Bring up OpenTelemetry. Soft-fails if the SDK or the OTLP
# endpoint is unreachable — the hub keeps serving traffic and the SPA's
# Telemetry tab shows "stack offline" until Langfuse comes up.
init_otel("local-llm-hub")


# ---- FastAPI app ----

app = FastAPI(title="Local LLM Hub", version="0.3.0")
# Errors raised on the Anthropic-shape routes serialise as Anthropic's
# {"type": "error", "error": {...}} envelope instead of FastAPI's
# {"detail": ...}, so the anthropic SDK's typed exceptions and
# retry logic behave against the hub as they do against the real API.
# Path-scoped: /v1/chat/completions keeps its existing shape.
install_anthropic_error_handlers(app)


# Bearer-token gate on the parent app. Loopback callers bypass; non-
# loopback callers must present the token (or be in the configured
# extra_allowlist). The /admin sub-app has its own copy of this
# middleware — its prefix is exempted here so a single auth boundary
# governs the whole process.
def _hub_get_token() -> Optional[str]:
    """Resolve the bearer token from config/webapp_config.json on every
    check so the user can edit it without restarting the hub.

    ``""`` means no token is configured (enforcement off by design);
    ``None`` means the config could not be loaded — including a malformed
    file, which the loader reports as ``WebappConfigError`` rather than
    defaults (#585) — so the token is *unknown*, and the middleware must
    not read that as "no token" and open the gate (#558).
    """
    try:
        from .webapp_config import load_webapp_config
        return getattr(load_webapp_config(), "auth_token", "") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ could not load webapp_config for the bearer gate: %s", exc)
        return None


from app_web.middleware import ParentBearerTokenMiddleware  # noqa: E402

app.add_middleware(ParentBearerTokenMiddleware, get_token=_hub_get_token)

# Observability middleware records every model call (chat, audio, images)
# into an in-memory ring read by the admin webapp's Hub tab and finalises its
# OTel span. Added after the bearer gate so it sits outside it: a rejected
# caller is a failed call and is recorded too. Volatile by design; the durable
# telemetry layer is the OTel + Langfuse stack bootstrapped by init_otel().
app.add_middleware(ObservatoryMiddleware)

# OTel ASGI instrumentation — added before the X-Trace-Id outer middleware
# so the OTel layer creates spans first; the X-Trace-Id wrapper then sees
# a live span context and can echo its trace ID to the client.
instrument_fastapi_app(app)

# X-Trace-Id contract — accept client-supplied UUID4 / hex in, always
# emit the current span's trace ID out. Pure-ASGI middleware; added last
# so it sits OUTERMOST.
app.add_middleware(TraceIdHeaderMiddleware)


# Expose the same WebappConfig to the parent app so the middleware can
# read ``extra_allowlist`` without re-loading on every request. Note the
# token itself is *not* cached — we always re-read so the user can
# rotate without restarting.
def _load_startup_webapp_config() -> Optional[WebappConfig]:
    """The startup snapshot, or ``None`` when the config can't be loaded.

    ``None`` is the restrictive side for both readers, so startup carries
    on: the middleware applies no ``extra_allowlist`` entries and
    ``install_cors`` allows loopback origins only. Logged, because the
    configured extras are silently absent until a restart otherwise (#585).
    """
    from .webapp_config import load_webapp_config

    try:
        return load_webapp_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "⚠️ could not load webapp_config at startup (%s); "
            "extra_allowlist and cors_allow_origins are off until a restart",
            exc,
        )
        return None


app.state.webapp_config = _load_startup_webapp_config()

# CORS for browser-based clients — added after every layer above so it
# sits outside them, including the bearer gate. A preflight OPTIONS
# carries no Authorization header by the browser's design, so it has to
# be answered here rather than 401'd downstream; real requests still
# travel the full stack and meet the gate unchanged. Loopback origins are
# allowed by default, extra origins are named in webapp_config.json, and
# a wildcard never ships — the policy and its rationale live in
# src/cors_policy.py.
install_cors(app, getattr(app.state, "webapp_config", None))

# Response compression — added after CORS so it is the true outermost layer:
# an auth failure, a 304 or a preflight reply is bodyless and passes through
# unchanged. Event streams and websockets are never compressed by the
# middleware itself, and the audio / image content types it skips cover every
# media route here, so no path needs a bypass.
app.add_middleware(StreamSafeGZipMiddleware, bypass_paths=())


# Startup/shutdown handlers + the background resource sampler live in
# server_lifecycle.py — server.py stays app construction +
# route registration.
from .server_lifecycle import register as _register_lifecycle  # noqa: E402

_register_lifecycle(app)


# Mount the admin sub-app at /admin. Done at import time so a fresh
# uvicorn workers picks it up; the sub-app has its own bearer-token
# middleware, separate from the parent hub.
def _mount_admin() -> None:
    # Guard against double-mount when the module is imported twice (e.g.
    # `python -m src.server` loads us as ``__main__`` and uvicorn then
    # re-imports as ``src.server`` to resolve the ``src.server:app``
    # spec).
    if any(getattr(r, "name", None) == "admin" for r in app.routes):
        return
    try:
        from app_web import create_app as _create_admin
        admin_app = _create_admin()
        admin_app.state.parent = app
        app.mount("/admin", admin_app, name="admin")
        logger.info("ℹ️ /admin sub-app mounted")
    except Exception as exc:  # noqa: BLE001
        logger.error("⚠️ /admin sub-app failed to mount: %s", exc)


_mount_admin()


@app.get("/", include_in_schema=False)
def root() -> Response:
    # Old landing page is gone — / now redirects to the admin webapp.
    return RedirectResponse(url="/admin/", status_code=307)


@app.get("/info", include_in_schema=False)
def info() -> Dict[str, Any]:
    return {
        "name": "Local LLM Hub",
        "version": app.version,
        "description": "Multi-model hub: Anthropic-shape + OpenAI-shape over Claude / Gemini / Qwen / GLM.",
        "endpoints": {
            "health": "GET /health",
            "audio_health": "GET /v1/audio/health",
            "messages": "POST /v1/messages",
            "count_tokens": "POST /v1/messages/count_tokens",
            "chat_completions": "POST /v1/chat/completions",
            "models": "GET /v1/models",
            "systemone": "POST /v1/systemone",
            "docs": "GET /docs",
        },
        "models": sorted({m.display_name for m in enabled_models()}),
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
def list_models() -> Dict[str, Any]:
    data = []
    for m in enabled_models():
        # Don't advertise a Gemini picker model agy is known not to offer.
        # Unknown catalogue (None) keeps the row listed — only a positive
        # "not in the catalogue" hides it.
        if (m.backend == "gemini" and not m.image_gen
                and gemini_cli.is_model_offered(m.display_name) is False):
            continue
        for name in m.all_names:
            data.append({
                "id": name,
                "object": "model",
                "owned_by": m.backend,
                "backend": m.backend,
            })
    return {"object": "list", "data": data}


# The chat routes live in src/routes/; include order keeps them registered
# ahead of the image, audio and TypeSafe routers, as when they were inline.
app.include_router(_anthropic_router)
app.include_router(_openai_router)


# ---- Image, audio + TypeSafe routes (split into sibling modules) ----
# The /v1/images/* handlers live in server_images.py and the TypeSafe
# /v1/systemone passthrough in server_systemone.py; the /v1/audio/* proxy
# is split across server_audio_asr.py (transcriptions/translations/health —
# owns the router object) and server_audio_tts.py (speech, registered onto
# that same router via the import above). All are plain APIRouters
# mounted here so the admin sub-app's auth boundary and the observability
# middleware still cover them. See those modules for the handler bodies.
app.include_router(_images_router)
app.include_router(_audio_router)
app.include_router(_otel_receiver_router)
app.include_router(_systemone_router)


def main() -> None:
    import uvicorn
    from src.event_loop import LOOP_FACTORY
    uvicorn.run(
        "src.server:app",
        host=hub_bind_host(),
        port=hub_port(),
        reload=False,
        loop=LOOP_FACTORY,
    )


if __name__ == "__main__":
    main()


