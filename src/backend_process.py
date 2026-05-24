"""Per-model backend process manager.

Each enabled local model in the registry gets its own singleton process +
log ring buffer here. Keyed by model id ("qwen", "glm", "whisper"). Used
by the Streamlit Models view, the tray, and the per-model launcher
scripts to start/stop individual backends and tail their output without
global-state entanglement.

Two engine families share this manager:
  - `llama-server` for chat/completion GGUF models (qwen, glm, gemma4*)
  - `whisper-server` for whisper.cpp ASR (OpenAI-compatible /v1/audio/*)
The shape differences (binary location, -m vs --model flag, health
endpoint) are absorbed in `build_command` and `is_reachable`.

Ownership semantics mirror :mod:`src.server_process` — see its module
docstring. ``start(model_id)`` adopts an already-reachable backend on
the model's port instead of spawning a duplicate; ``stop(model_id)``
only stops what we spawned. Use :func:`force_stop_external` to reclaim
a port held by someone else.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Deque, Dict, Optional

import httpx

from .model_registry import Model, enabled_models, resolve as resolve_model
from .server_process import (
    OWNERSHIP_EXTERNAL,
    OWNERSHIP_NONE,
    OWNERSHIP_OURS,
    find_port_pids,
    kill_pid,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_LLAMA = PROJECT_ROOT / "vendor" / "llama.cpp"
VENDOR_WHISPER = PROJECT_ROOT / "vendor" / "whisper.cpp"
RING_MAX = 1000

# On Windows, give the child its own process group so CTRL_BREAK_EVENT
# during stop() doesn't propagate to the tray launcher, and suppress the
# console so silent parents (pythonw, e.g. the tray) don't spawn a
# stray cmd window for native binaries like llama-server.exe.
_WIN_NEW_GROUP = (
    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    if sys.platform == "win32" else 0
)


def _llama_server_binary() -> Path:
    name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    return VENDOR_LLAMA / name


def _whisper_server_binary() -> Path:
    name = "whisper-server.exe" if sys.platform == "win32" else "whisper-server"
    return VENDOR_WHISPER / name


def _is_whisper(model: Model) -> bool:
    return (
        model.engine == "whisper-server"
        or model.engine == "whisper-server-lazy"
        or model.backend == "whisper"
    )


def _is_lazy_whisper(model: Model) -> bool:
    return model.engine == "whisper-server-lazy"


def _vendor_dir_for(model: Model) -> Path:
    return VENDOR_WHISPER if _is_whisper(model) else VENDOR_LLAMA


class _BackendState:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        # PID of a backend the hub inherited at startup — a process the
        # hub didn't itself spawn but recognises as one of its model
        # binaries listening on the right port. ``stop`` taskkill's it
        # instead of calling ``proc.terminate``; ``log_lines`` returns
        # empty since we never captured its stdout.
        self.inherited_pid: Optional[int] = None
        self.log: Deque[str] = deque(maxlen=RING_MAX)
        self.lock = threading.Lock()
        self.reader: Optional[threading.Thread] = None


_STATES: Dict[str, _BackendState] = {}


def _state_for(model_id: str) -> _BackendState:
    state = _STATES.get(model_id)
    if state is None:
        state = _BackendState()
        _STATES[model_id] = state
    return state


def is_running(model_id: str) -> bool:
    state = _state_for(model_id)
    p = state.proc
    if p is not None and p.poll() is None:
        return True
    return _inherited_alive(state)


def is_inherited(model_id: str) -> bool:
    """True iff this model is alive via an inherited PID (not a Popen we own)."""
    state = _state_for(model_id)
    return state.proc is None and _inherited_alive(state)


def pid(model_id: str) -> Optional[int]:
    state = _state_for(model_id)
    p = state.proc
    if p is not None and p.poll() is None:
        return p.pid
    if _inherited_alive(state):
        return state.inherited_pid
    return None


def _inherited_alive(state: "_BackendState") -> bool:
    pid_ = state.inherited_pid
    if pid_ is None:
        return False
    try:
        import psutil

        if not psutil.pid_exists(pid_):
            state.inherited_pid = None
            return False
        # Verify it's still the same process — PID reuse on Windows is
        # aggressive; if the create-time has changed, our PID is stale.
        proc = psutil.Process(pid_)
        if proc.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
            state.inherited_pid = None
            return False
        return True
    except Exception:  # noqa: BLE001
        state.inherited_pid = None
        return False


def is_reachable(model: Model, timeout: float = 1.5) -> bool:
    if not model.url:
        return False
    base = model.url.rstrip("/v1").rstrip("/")
    if _is_whisper(model):
        # whisper.cpp server has no /health; GET / returns 200 once loaded.
        try:
            r = httpx.get(f"{base}/", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False
    try:
        r = httpx.get(f"{base}/health", timeout=timeout)
        if r.status_code == 200:
            return True
    except Exception:
        pass
    # llama-server /v1/models is always available once loaded
    try:
        r = httpx.get(f"{model.url}/models", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def log_lines(model_id: str) -> list[str]:
    state = _state_for(model_id)
    with state.lock:
        return list(state.log)


def clear_log(model_id: str) -> None:
    state = _state_for(model_id)
    with state.lock:
        state.log.clear()


def _reader(state: _BackendState, proc: subprocess.Popen) -> None:
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        with state.lock:
            state.log.append(line)


def build_command(model: Model) -> list[str]:
    if not model.model_path:
        raise RuntimeError(f"model {model.id} has no model_path")
    model_path = (PROJECT_ROOT / model.model_path).resolve()

    if _is_lazy_whisper(model):
        # The proxy itself doesn't need the model on disk to start — it
        # only needs whisper-server present. We still surface a clear
        # error if the model is missing, since the first POST would fail.
        bin_path = _whisper_server_binary()
        if not bin_path.exists():
            raise RuntimeError(
                f"whisper-server not found at {bin_path} - run scripts/install_whisper_cpp.py"
            )
        if not model_path.exists():
            raise RuntimeError(
                f"whisper model not found at {model_path} - run scripts/download_models.py --only {model.id}"
            )
        return [
            sys.executable, "-m", "src.whisper_translate_proxy",
            "--model-id", model.id,
        ]

    if _is_whisper(model):
        bin_path = _whisper_server_binary()
        if not bin_path.exists():
            raise RuntimeError(
                f"whisper-server not found at {bin_path} - run scripts/install_whisper_cpp.py"
            )
        if not model_path.exists():
            raise RuntimeError(
                f"whisper model not found at {model_path} - run scripts/download_models.py --only {model.id}"
            )
        cmd = [
            str(bin_path),
            "--host", "0.0.0.0",
            "--port", str(model.port),
            "--model", str(model_path),
        ]
        cmd.extend(model.args or [])
        return cmd

    bin_path = _llama_server_binary()
    if not bin_path.exists():
        raise RuntimeError(f"llama-server not found at {bin_path} - run scripts/install_llama_cpp.py")
    if not model_path.exists():
        raise RuntimeError(f"GGUF not found at {model_path} - run scripts/download_models.py --only {model.id}")
    cmd = [
        str(bin_path),
        "-m", str(model_path),
        "--host", "0.0.0.0",
        "--port", str(model.port),
    ]
    cmd.extend(model.args or [])
    return cmd


def start(model_id: str) -> tuple[bool, str]:
    model = resolve_model_by_id(model_id)
    if model is None:
        return False, f"model {model_id!r} not enabled on this host"
    if is_running(model_id):
        return False, "already running"

    # Adopt an external instance already listening on this model's port.
    if is_reachable(model, timeout=0.4):
        ext = external_pid(model_id)
        suffix = f" (PID {ext})" if ext else ""
        return True, f"adopted external instance{suffix}"

    state = _state_for(model_id)
    clear_log(model_id)

    try:
        cmd = build_command(model)
    except Exception as e:
        return False, str(e)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # Help the server find the cudart DLLs shipped next to its binary.
    if sys.platform == "win32":
        env["PATH"] = str(_vendor_dir_for(model)) + os.pathsep + env.get("PATH", "")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=_WIN_NEW_GROUP,
        )
    except Exception as e:
        return False, f"failed to launch: {e}"

    state.proc = proc
    t = threading.Thread(target=_reader, args=(state, proc), daemon=True)
    t.start()
    state.reader = t
    return True, f"started (pid={proc.pid})"


def stop(model_id: str) -> tuple[bool, str]:
    state = _state_for(model_id)
    p = state.proc
    # Inherited backend: we don't hold a Popen handle, so polite shutdown
    # isn't an option — taskkill the PID directly.
    if p is None:
        if _inherited_alive(state):
            pid_ = state.inherited_pid
            state.inherited_pid = None
            ok, msg = kill_pid(int(pid_))
            return ok, msg
        return False, "not running"
    if p.poll() is not None:
        state.proc = None
        return False, "not running"
    try:
        if sys.platform == "win32":
            try:
                p.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception:
                pass
        p.terminate()
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)
    except Exception as e:
        return False, f"error stopping: {e}"
    state.proc = None
    return True, "stopped"


def inherit_running_backends() -> int:
    """Adopt any model-backend process the hub finds on one of its ports.

    Called once at hub startup. Without this, a hub restart leaves the
    previous hub's children alive on their ports — the new hub sees
    them as "external" (adopted) and the UI shows the disabled-Stop
    state. With inheritance, the new hub treats them as ours, the UI
    shows them as running, and Stop force-kills the PID directly.

    Returns the number of backends inherited.
    """
    from .server_process import snapshot_listening_pids

    try:
        import psutil
    except ImportError:
        return 0

    listening = snapshot_listening_pids()
    count = 0
    for m in enabled_models():
        if m.backend not in ("openai", "whisper"):
            continue
        if not m.port:
            continue
        if is_running(m.id):
            continue  # already ours (Popen or earlier-inherited)
        pids = listening.get(m.port) or []
        if not pids:
            continue
        candidate = pids[0]
        try:
            proc = psutil.Process(candidate)
            exe = (proc.exe() or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        if _looks_like_backend_binary(exe, m):
            state = _state_for(m.id)
            state.inherited_pid = candidate
            count += 1
            import logging
            logging.getLogger(__name__).info(
                "📎 Inherited %s on :%s (PID %s) — log tail unavailable",
                m.id, m.port, candidate,
            )
    return count


def _looks_like_backend_binary(exe: str, model: "Model") -> bool:
    """Heuristic: does this executable look like the binary we'd spawn for ``model``?"""
    exe = (exe or "").lower()
    if model.engine in ("whisper-server", "whisper-server-lazy") or model.backend == "whisper":
        return "whisper-server" in exe or exe.endswith("whisper-server.exe")
    # Default: llama.cpp's llama-server. The lazy-whisper proxy runs as
    # ``python -m src.whisper_translate_proxy`` — recognise pythonw too.
    return (
        "llama-server" in exe
        or "python" in exe  # whisper_translate_proxy.py path
    )


def resolve_model_by_id(model_id: str) -> Optional[Model]:
    for m in enabled_models():
        if m.id == model_id:
            return m
    return None


def running_backends() -> Dict[str, Model]:
    """Return {model_id: Model} for each local backend whose process is alive."""
    out: Dict[str, Model] = {}
    for m in enabled_models():
        if m.backend in ("openai", "whisper") and is_running(m.id):
            out[m.id] = m
    return out


def ownership(model_id: str) -> str:
    """Tri-state ownership of the port for *model_id* — see server_process docstring."""
    model = resolve_model_by_id(model_id)
    if model is None or model.port is None:
        return OWNERSHIP_NONE
    if is_running(model_id):
        return OWNERSHIP_OURS
    if find_port_pids(model.port):
        return OWNERSHIP_EXTERNAL
    return OWNERSHIP_NONE


def external_pid(model_id: str) -> Optional[int]:
    """PID holding *model_id*'s port if it isn't us, else ``None``."""
    if is_running(model_id):
        return None
    model = resolve_model_by_id(model_id)
    if model is None or model.port is None:
        return None
    pids = find_port_pids(model.port)
    return pids[0] if pids else None


def force_stop_external(model_id: str) -> tuple[bool, str]:
    """Force-kill whoever currently holds *model_id*'s port, if it's not us."""
    target = external_pid(model_id)
    if target is None:
        return False, "no external process on this model's port"
    return kill_pid(target)
