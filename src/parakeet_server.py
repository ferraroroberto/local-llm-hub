"""OpenAI-shape ASR server wrapping the FluidAudio Parakeet CoreML worker.

Binds the registry row's external ``port`` and exposes ``POST
/v1/audio/transcriptions`` accepting an OpenAI multipart upload
(``file``, optional ``model``), returning ``{"text": ...}``.

Launched by ``backend_process.build_command`` for any ``engine:
parakeet-server`` row as ``python -m src.parakeet_server --model-id
<id>`` — the same in-repo-shim pattern as ``tts_server``/
``whisper_translate_proxy``. The hub proxies ``/v1/audio/transcriptions``
to this port so requests land in the observability ring
(``src/server_audio.py``).

darwin-only: keeps one long-lived ``mac/parakeet-worker`` Swift subprocess
warm (the CoreML model loads once) and serializes requests through its
stdin/stdout — see ``mac/parakeet-worker/Sources/ParakeetWorker/main.swift``
for the worker side of this protocol (one JSON result per line). Spike
origin + benchmark results: #138, docs/parakeet-asr-evaluation.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .backend_process import resolve_model_by_id
from .model_registry import Model

log = logging.getLogger("parakeet_server")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKER_BIN = PROJECT_ROOT / "mac" / "parakeet-worker" / ".build" / "release" / "ParakeetWorker"

DEFAULT_MODEL_ID = "parakeet"


class _State:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.lock = asyncio.Lock()


def _resolve_model(model_id: str) -> Model:
    model = resolve_model_by_id(model_id)
    if model is None:
        raise SystemExit(
            f"model {model_id!r} not enabled on this host — "
            f"add it to the host's enabled list in config/models.yaml"
        )
    if model.engine != "parakeet-server":
        raise SystemExit(
            f"model {model_id!r} has engine={model.engine!r}; "
            f"this server only handles engine=parakeet-server"
        )
    return model


def _start_worker() -> subprocess.Popen:
    if not WORKER_BIN.exists():
        raise RuntimeError(
            f"ParakeetWorker binary missing at {WORKER_BIN} — "
            f"run `swift build -c release` in mac/parakeet-worker/ "
            f"(the admin Health & install panel's Fix-all does this too)"
        )
    proc = subprocess.Popen(
        [str(WORKER_BIN)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    line = proc.stdout.readline()
    while line and line.strip() != "READY":
        line = proc.stdout.readline()
    if not line:
        err = proc.stderr.read()
        proc.wait()
        raise RuntimeError(f"ParakeetWorker failed to start: {err}")
    log.info("ParakeetWorker ready (pid=%s)", proc.pid)
    return proc


def _to_wav16k_mono(src: Path) -> Path:
    dst = src.with_suffix(".norm.wav")
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(src), str(dst)],
        check=True, capture_output=True,
    )
    return dst


def build_app(model_id: str = DEFAULT_MODEL_ID) -> FastAPI:
    model = _resolve_model(model_id)
    state = _State()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            state.proc = await asyncio.to_thread(_start_worker)
        except Exception as exc:  # noqa: BLE001
            log.error("ParakeetWorker startup failed: %s", exc)
        try:
            yield
        finally:
            if state.proc is not None and state.proc.poll() is None:
                state.proc.terminate()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        alive = state.proc is not None and state.proc.poll() is None
        return {"status": "ok" if alive else "down", "model": model.display_name}

    @app.post("/v1/audio/transcriptions")
    async def transcribe(file: UploadFile = File(...)) -> JSONResponse:
        async with state.lock:
            if state.proc is None or state.proc.poll() is not None:
                log.warning("worker not alive, restarting")
                state.proc = await asyncio.to_thread(_start_worker)
            proc = state.proc

            with tempfile.TemporaryDirectory() as tmpdir:
                raw_path = Path(tmpdir) / (file.filename or "upload.bin")
                raw_path.write_bytes(await file.read())
                try:
                    wav_path = await asyncio.to_thread(_to_wav16k_mono, raw_path)
                except subprocess.CalledProcessError as exc:
                    raise HTTPException(
                        400, f"audio conversion failed: {exc.stderr.decode(errors='replace')}"
                    )

                def _roundtrip() -> str:
                    proc.stdin.write(str(wav_path) + "\n")
                    proc.stdin.flush()
                    return proc.stdout.readline()

                line = await asyncio.to_thread(_roundtrip)

            if not line:
                err = proc.stderr.read() if proc.stderr else ""
                raise HTTPException(500, f"worker died mid-request: {err}")

            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                raise HTTPException(500, f"worker returned non-JSON: {line!r}")

            if not resp.get("ok"):
                raise HTTPException(500, f"transcription failed: {resp.get('error')}")

            return JSONResponse({"text": resp["text"]})

    return app


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    p = argparse.ArgumentParser(prog="python -m src.parakeet_server")
    p.add_argument("--model-id", default=DEFAULT_MODEL_ID, help="registry id of the parakeet-server row")
    args = p.parse_args(argv)

    model = _resolve_model(args.model_id)
    if not model.port:
        raise SystemExit(f"model {model.id!r} has no port configured")

    app = build_app(args.model_id)
    uvicorn.run(app, host="0.0.0.0", port=model.port, log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
