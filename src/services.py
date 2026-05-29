"""Host-side service helpers — Docker engine + Langfuse stack (issue #27).

The hub depends on a running Docker engine for the Langfuse observability
stack, but Docker Desktop is user-managed and silently down after a reboot
is a common failure mode. This module gives the admin SPA's Hub tab a way
to (a) tell the user that Docker / Langfuse are down and (b) bring them
back up with one button.

Everything here is best-effort and soft-failing: probes have short
timeouts, launches return structured step logs rather than raising, and
the Langfuse health probe degrades cleanly when the SDK / containers
are not present.

Sibling to ``server_process.py`` (hub-process lifecycle) and
``backend_process.py`` (per-model llama-server / whisper-server
lifecycle).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.observability import langfuse_host

logger = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DOCKER_PROBE_TIMEOUT_S = 2.0
LANGFUSE_PROBE_TIMEOUT_S = 2.0

# Used by POST /admin/api/services/launch — total budget for `docker info`
# to start succeeding after we spawn Docker Desktop. The engine usually
# comes up in 10-30 s on Windows; allow some slack.
DOCKER_READY_TIMEOUT_S = 90.0
# Same idea for Langfuse after `start_langfuse.bat` returns — image pulls
# already happened on first run, so steady-state is ~30 s.
LANGFUSE_READY_TIMEOUT_S = 90.0


# Windows install candidates for Docker Desktop. First-existing wins.
# Probe both Program Files and the per-user install location.
_WINDOWS_DOCKER_DESKTOP_CANDIDATES = (
    r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Docker\Docker\Docker Desktop.exe"),
)


# ---------------------------------------------------------------- helpers


def find_docker_desktop() -> Optional[Path]:
    """Locate the Docker Desktop executable on this host, or None.

    Windows-only: macOS launches Docker via ``open -a Docker`` and Linux
    typically runs the engine under systemd with no GUI to launch.
    """
    if sys.platform != "win32":
        return None
    for candidate in _WINDOWS_DOCKER_DESKTOP_CANDIDATES:
        p = Path(candidate)
        if p.exists():
            return p
    return None


def langfuse_start_script() -> Path:
    """Return the platform-appropriate start_langfuse script path."""
    if sys.platform == "win32":
        return PROJECT_ROOT / "start_langfuse.bat"
    return PROJECT_ROOT / "start_langfuse.sh"


def _langfuse_basic_auth() -> Optional[str]:
    """Return ``Basic <base64(pk:sk)>`` from env, or None when missing.

    Duplicates the helper in ``app_web/routers/telemetry.py`` — kept
    separate so this module has no import dependency on the admin
    sub-app (which would create a cycle: services router imports this,
    this imports telemetry).
    """
    pk = (os.environ.get("LANGFUSE_PUBLIC_KEY") or "").strip()
    sk = (os.environ.get("LANGFUSE_SECRET_KEY") or "").strip()
    if not pk or not sk:
        return None
    return "Basic " + base64.b64encode(f"{pk}:{sk}".encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------- docker


async def docker_status(timeout_s: float = DOCKER_PROBE_TIMEOUT_S) -> Dict[str, Any]:
    """Probe the Docker engine. Returns ``{running, error}``.

    Uses ``docker info`` with a short timeout. Treats both "docker
    binary missing" and "daemon pipe missing" as ``running=False`` —
    the SPA card only needs the binary state.
    """
    if shutil.which("docker") is None:
        return {"running": False, "error": "docker CLI not on PATH"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "info", "--format", "{{.ServerVersion}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # CREATE_NO_WINDOW on Windows so this poll (fired every few
            # seconds while the Hub tab is open) doesn't flash a console
            # window — matching system_stats.gpu_stats / claude_cli.
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return {"running": False, "error": f"`docker info` timed out after {timeout_s:.1f}s"}
        if proc.returncode == 0:
            version = (stdout or b"").decode("utf-8", errors="replace").strip()
            return {"running": True, "error": "", "server_version": version}
        # Daemon down — keep the first line of stderr for the UI.
        err = (stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        first = err[0] if err else f"exit {proc.returncode}"
        return {"running": False, "error": first[:200]}
    except OSError as exc:
        return {"running": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------- langfuse


async def langfuse_health(timeout_s: float = LANGFUSE_PROBE_TIMEOUT_S) -> Dict[str, Any]:
    """Probe Langfuse's public health endpoint.

    Returns ``{reachable, status_code, error, host}``. ``reachable`` is
    True only when the server returns < 500; auth keys are optional for
    the health endpoint itself.
    """
    host = langfuse_host()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(f"{host}/api/public/health")
        return {
            "reachable": r.status_code < 500,
            "status_code": r.status_code,
            "error": "" if r.status_code < 500 else f"HTTP {r.status_code}",
            "host": host,
        }
    except Exception as exc:  # noqa: BLE001 — network / connection / DNS
        return {
            "reachable": False,
            "status_code": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "host": host,
        }


# ---------------------------------------------------------------- launch


def _spawn_docker_desktop(exe: Path) -> None:
    """Start Docker Desktop detached so it survives the request.

    Windows: CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS keeps it alive
    after the uvicorn worker that handled the launch request moves on.
    """
    creationflags = 0
    if sys.platform == "win32":
        DETACHED = 0x00000008
        creationflags = (
            DETACHED
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    subprocess.Popen(
        [str(exe)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )


async def wait_for_docker(
    timeout_s: float = DOCKER_READY_TIMEOUT_S,
    poll_s: float = 2.0,
) -> bool:
    """Poll ``docker info`` until it succeeds or the budget expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        info = await docker_status(timeout_s=DOCKER_PROBE_TIMEOUT_S)
        if info["running"]:
            return True
        await asyncio.sleep(poll_s)
    return False


async def wait_for_langfuse(
    timeout_s: float = LANGFUSE_READY_TIMEOUT_S,
    poll_s: float = 3.0,
) -> bool:
    """Poll the Langfuse health endpoint until it responds < 500."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        info = await langfuse_health(timeout_s=LANGFUSE_PROBE_TIMEOUT_S)
        if info["reachable"]:
            return True
        await asyncio.sleep(poll_s)
    return False


async def _run_langfuse_start_script() -> Dict[str, Any]:
    """Run ``start_langfuse.{bat,sh}`` and capture the result.

    Returns ``{ok, returncode, stdout, stderr}``. The script itself is
    idempotent (``docker compose up -d``) so calling it again on an
    already-running stack is a fast no-op.
    """
    script = langfuse_start_script()
    if not script.exists():
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"start script not found: {script}",
        }
    if sys.platform == "win32":
        cmd = ["cmd.exe", "/c", str(script)]
    else:
        cmd = ["/bin/sh", str(script)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (stdout or b"").decode("utf-8", errors="replace"),
            "stderr": (stderr or b"").decode("utf-8", errors="replace"),
        }
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "start_langfuse script timed out after 120 s",
        }
    except OSError as exc:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }


async def launch_stack() -> Dict[str, Any]:
    """End-to-end recovery: start Docker Desktop if down, then Langfuse.

    Returns ``{ok, steps: [{name, status, detail}]}`` where each step
    is ``ok`` / ``skipped`` / ``error``. The first error short-circuits
    the rest of the chain.
    """
    steps: List[Dict[str, str]] = []

    # ----- step 1: docker engine
    info = await docker_status()
    if info["running"]:
        steps.append({"name": "docker_engine", "status": "skipped", "detail": "engine already up"})
    else:
        if sys.platform != "win32":
            steps.append({
                "name": "docker_engine",
                "status": "error",
                "detail": (
                    "auto-launch is Windows-only — start Docker manually "
                    "(`open -a Docker` on macOS, `sudo systemctl start docker` on Linux)"
                ),
            })
            return {"ok": False, "steps": steps}
        exe = find_docker_desktop()
        if exe is None:
            steps.append({
                "name": "docker_engine",
                "status": "error",
                "detail": (
                    "Docker Desktop install not found in Program Files or LOCALAPPDATA — "
                    "install it from docker.com/products/docker-desktop"
                ),
            })
            return {"ok": False, "steps": steps}
        try:
            _spawn_docker_desktop(exe)
        except OSError as exc:
            steps.append({
                "name": "docker_engine",
                "status": "error",
                "detail": f"spawn failed: {type(exc).__name__}: {exc}",
            })
            return {"ok": False, "steps": steps}
        ready = await wait_for_docker()
        if not ready:
            steps.append({
                "name": "docker_engine",
                "status": "error",
                "detail": (
                    f"engine still not responsive after {DOCKER_READY_TIMEOUT_S:.0f}s — "
                    "Docker Desktop may have shown a prompt; check the system tray"
                ),
            })
            return {"ok": False, "steps": steps}
        steps.append({
            "name": "docker_engine",
            "status": "ok",
            "detail": f"started Docker Desktop ({exe})",
        })

    # ----- step 2: langfuse stack
    health = await langfuse_health()
    if health["reachable"]:
        steps.append({"name": "langfuse_stack", "status": "skipped", "detail": "stack already up"})
        return {"ok": True, "steps": steps}

    result = await _run_langfuse_start_script()
    if not result["ok"]:
        # First line of stderr is usually the actionable bit.
        err_lines = [ln for ln in (result["stderr"] or "").splitlines() if ln.strip()]
        detail = err_lines[0][:200] if err_lines else f"exit {result['returncode']}"
        steps.append({"name": "langfuse_stack", "status": "error", "detail": detail})
        return {"ok": False, "steps": steps}
    ready = await wait_for_langfuse()
    if not ready:
        steps.append({
            "name": "langfuse_stack",
            "status": "error",
            "detail": (
                f"containers started but /api/public/health unreachable after "
                f"{LANGFUSE_READY_TIMEOUT_S:.0f}s — check `docker compose -f "
                "docker/langfuse/docker-compose.yml ps` for container errors"
            ),
        })
        return {"ok": False, "steps": steps}

    steps.append({
        "name": "langfuse_stack",
        "status": "ok",
        "detail": "containers started and health endpoint responding",
    })
    return {"ok": True, "steps": steps}
