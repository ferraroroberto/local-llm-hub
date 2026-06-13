"""Cross-platform backend dispatcher.

    python -m src.run_backend hub    # run the FastAPI hub on port 8000
    python -m src.run_backend qwen   # run llama-server with the qwen entry
    python -m src.run_backend glm    # run llama-server with the glm entry

Each .bat / .sh launcher is a one-liner that calls this module.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

from .backend_process import (
    build_command,
    external_pid as backend_external_pid,
    is_reachable as backend_is_reachable,
    resolve_model_by_id,
    vendor_dir_for,
)
from .host_profile import resolve as resolve_host
from .model_registry import enabled_models
from .server_process import (
    BASE_URL as HUB_BASE_URL,
    external_pid as hub_external_pid,
    is_reachable as hub_is_reachable,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_hub() -> int:
    # Adopt: if the hub is already up (e.g. started by the tray or another
    # `run_hub` window), don't try to bind :8000 a second time — uvicorn
    # would crash with WinError 10048. Print and exit cleanly so the user
    # can see what happened in the launcher's terminal.
    if hub_is_reachable(timeout=0.4):
        ext = hub_external_pid()
        suffix = f" (PID {ext})" if ext else ""
        log.info("hub already running at %s%s — nothing to do.", HUB_BASE_URL, suffix)
        return 0
    from . import server
    server.main()
    return 0


def _run_backend(model_id: str) -> int:
    host = resolve_host()
    model = resolve_model_by_id(model_id)
    if model is None:
        known = [m.id for m in enabled_models()]
        log.error("model %r not enabled on host %s. known: %s", model_id, host.id, known)
        return 2
    if model.backend not in ("openai", "whisper", "tts"):
        log.error("model %r is backend=%s; nothing to spawn", model_id, model.backend)
        return 2

    # Same adopt-check as the hub: skip if something already answers on
    # this model's port.
    if backend_is_reachable(model, timeout=0.4):
        ext = backend_external_pid(model_id)
        suffix = f" (PID {ext})" if ext else ""
        log.info("%s already running on :%s%s — nothing to do.", model.display_name, model.port, suffix)
        return 0

    cmd = build_command(model)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if sys.platform == "win32":
        env["PATH"] = str(vendor_dir_for(model)) + os.pathsep + env.get("PATH", "")
    log.info("-> %s", " ".join(cmd))
    # Foreground execution so Ctrl+C works.
    return subprocess.call(cmd, env=env, cwd=str(PROJECT_ROOT))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        log.error("usage: python -m src.run_backend (hub|<model_id>)")
        return 2
    target = args[0]
    if target == "hub":
        return _run_hub()
    return _run_backend(target)


if __name__ == "__main__":
    sys.exit(main())
