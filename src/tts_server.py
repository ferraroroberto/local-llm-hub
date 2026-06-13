"""OpenAI-shape text-to-speech server — the inverse of the whisper STT pair.

Binds the registry row's external ``port`` (chatterbox :8092 / orpheus
:8093) and exposes ``POST /v1/audio/speech`` accepting the OpenAI body
``{model, input, voice, response_format, speed}`` (plus Chatterbox's
``exaggeration`` / ``cfg_weight`` tone dial) and returning audio bytes.

Launched by ``backend_process.build_command`` for any ``engine: tts-server``
row as ``python -m src.tts_server --model-id <id>`` — the same in-repo-shim
pattern as ``whisper_translate_proxy``. The hub proxies ``/v1/audio/speech``
to this port so requests land in the observability ring (``src/server.py``).

The heavy synthesis engine (torch/chatterbox/snac) loads in a **background
thread** after startup, so the port answers ``GET /health`` immediately
(``ready`` flips true once the model is warm). Synthesis returns 503 while
loading and surfaces any load error verbatim.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import threading
import wave
from contextlib import asynccontextmanager
from typing import Optional, Tuple

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from .backend_process import resolve_model_by_id
from .model_registry import Model
from .tts_engines import SpeechRequest, TTSEngine, build_engine

log = logging.getLogger("tts_server")

DEFAULT_MODEL_ID = "chatterbox"


class _State:
    def __init__(self) -> None:
        self.engine: Optional[TTSEngine] = None
        self.ready: bool = False
        self.loading: bool = True
        self.error: str = ""
        self.device: str = ""
        self.sample_rate: int = 24000


def _float(body: dict, key: str, default: float) -> float:
    try:
        v = body.get(key)
        return default if v is None else float(v)
    except (TypeError, ValueError):
        return default


def _wav_bytes(samples, sample_rate: int) -> bytes:
    import numpy as np

    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


def encode_audio(samples, sample_rate: int, fmt: str) -> Tuple[bytes, str]:
    """Encode mono float32 samples to the requested ``response_format``.

    ``wav`` (default) and ``pcm`` are produced with the stdlib — no extra
    deps. ``flac`` / ``ogg`` / ``opus`` / ``mp3`` / ``aac`` are attempted via
    ``soundfile`` and fall back to wav (with a logged note) when the
    encoder isn't available, so a request never fails on format alone.
    """
    import numpy as np

    fmt = (fmt or "wav").strip().lower()
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    if fmt in ("wav", "wave", ""):
        return _wav_bytes(pcm, sample_rate), "audio/wav"
    if fmt == "pcm":
        return (pcm * 32767.0).astype("<i2").tobytes(), "audio/L16"

    sf_format = {"flac": "FLAC", "ogg": "OGG", "opus": "OGG", "mp3": "MP3", "aac": "MP3"}.get(fmt)
    media = {
        "flac": "audio/flac", "ogg": "audio/ogg", "opus": "audio/ogg",
        "mp3": "audio/mpeg", "aac": "audio/mpeg",
    }.get(fmt)
    if sf_format is not None:
        try:
            import soundfile as sf

            buf = io.BytesIO()
            sf.write(buf, pcm, int(sample_rate), format=sf_format)
            return buf.getvalue(), media or "application/octet-stream"
        except Exception as exc:  # noqa: BLE001
            log.warning("format %r unavailable (%s) — returning wav", fmt, exc)
    else:
        log.warning("unknown response_format %r — returning wav", fmt)
    return _wav_bytes(pcm, sample_rate), "audio/wav"


def _resolve_model(model_id: str) -> Model:
    model = resolve_model_by_id(model_id)
    if model is None:
        raise SystemExit(
            f"model {model_id!r} not enabled on this host — "
            f"add it to the host's enabled list in config/models.yaml"
        )
    if model.engine != "tts-server":
        raise SystemExit(
            f"model {model_id!r} has engine={model.engine!r}; "
            f"this server only handles engine=tts-server"
        )
    return model


def build_app(model_id: str = DEFAULT_MODEL_ID, device: str = "auto") -> FastAPI:
    model = _resolve_model(model_id)
    state = _State()

    def _load() -> None:
        try:
            engine = build_engine(model, device)
            engine.load()
            state.engine = engine
            state.sample_rate = engine.sample_rate
            # Report the *resolved* device (cuda/cpu/mps) the engine chose,
            # not the "auto" arg — the admin UI needs to show GPU vs CPU.
            state.device = getattr(engine, "device", device) or device
            state.ready = True
            log.info("%s ready on :%s (engine=%s)", model.display_name, model.port, model.tts_engine)
        except Exception as exc:  # noqa: BLE001
            state.error = f"{type(exc).__name__}: {exc}"
            log.error("TTS engine load failed: %s", state.error)
        finally:
            state.loading = False

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        state.device = device
        threading.Thread(target=_load, name="tts-load", daemon=True).start()
        try:
            yield
        finally:
            if state.engine is not None:
                try:
                    state.engine.close()
                except Exception:  # noqa: BLE001
                    pass

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {
            "ok": True,
            "model": model.display_name,
            "engine": model.tts_engine,
            "device": state.device,
            "ready": state.ready,
            "loading": state.loading,
            "error": state.error,
            "sample_rate": state.sample_rate,
        }

    @app.get("/")
    async def root() -> Response:
        status = "ready" if state.ready else ("loading" if state.loading else f"error: {state.error}")
        body = (
            f"tts_server: {model.display_name} ({model.tts_engine})\n"
            f"  port    : {model.port}\n"
            f"  device  : {state.device}\n"
            f"  status  : {status}\n"
            f"  POST /v1/audio/speech  {{model, input, voice, response_format, speed}}\n"
        )
        return Response(content=body, media_type="text/plain")

    @app.post("/v1/audio/speech")
    async def speech(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")

        text = body.get("input") or body.get("text")
        if not text or not str(text).strip():
            raise HTTPException(status_code=400, detail="missing required field: input")

        if not state.ready:
            if state.error:
                raise HTTPException(status_code=503, detail=f"TTS engine unavailable: {state.error}")
            raise HTTPException(status_code=503, detail="TTS engine still loading — retry shortly")

        speed = _float(body, "speed", 1.0)
        if abs(speed - 1.0) > 1e-3:
            # Neither Chatterbox nor Orpheus exposes a native rate control;
            # documented no-op (issue #98 acceptance allows this).
            log.info("speed=%.2f requested but is a no-op for this engine", speed)

        req = SpeechRequest(
            text=str(text),
            voice=str(body.get("voice") or ""),
            speed=speed,
            exaggeration=_float(body, "exaggeration", 0.5),
            cfg_weight=_float(body, "cfg_weight", 0.5),
        )
        assert state.engine is not None
        try:
            samples = await run_in_threadpool(state.engine.synthesize, req)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"synthesis failed: {exc}")

        fmt = str(body.get("response_format") or "wav")
        audio, media_type = encode_audio(samples, state.sample_rate, fmt)
        return Response(content=audio, media_type=media_type)

    return app


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    p = argparse.ArgumentParser(prog="python -m src.tts_server")
    p.add_argument("--model-id", default=DEFAULT_MODEL_ID, help="registry id of the tts-server row")
    p.add_argument("--device", default="auto", help="auto|cuda|cpu|mps")
    args = p.parse_args(argv)

    model = _resolve_model(args.model_id)
    if not model.port:
        raise SystemExit(f"model {model.id!r} has no port configured")

    app = build_app(args.model_id, args.device)
    uvicorn.run(app, host="0.0.0.0", port=model.port, log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
