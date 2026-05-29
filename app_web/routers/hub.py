"""Hub tab API — status, control, live request stream, log tail, install.

Endpoints (all under /admin/api/hub):
  * GET  /status            — pid, uptime, local/lan URLs, build identity
  * POST /stop              — graceful shutdown (the page will then 502)
  * POST /restart           — spawn a watchdog that respawns ``src.server``
  * GET  /log/tail          — SSE stream of root-logger lines
  * GET  /log/recent        — non-SSE seed (last N lines)
  * GET  /stats             — 5-minute ring of RAM/GPU samples (sparklines)
  * GET  /requests/stream   — SSE stream of every routed /v1/* request
  * GET  /requests/recent   — non-SSE seed (last N records)
  * GET  /errors/recent     — non-2xx ring
  * GET  /counters          — per-backend counters since hub start

Plus /admin/api/install/{status,fix-all} which fold in the old install tab.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.hub_log import HUB_LOG
from src.hub_observability import OBS

logger = logging.getLogger(__name__)
router = APIRouter()


# ----------------------------------------------------------------- helpers

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _hub_port() -> int:
    from src.host_profile import hub_port

    return int(hub_port())


def _lan_ip() -> str:
    """Best-effort outbound interface IP — same UDP-connect trick the
    legacy server_process module uses."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def _sse_pack(data: Any, event: str = "") -> str:
    body = data if isinstance(data, str) else json.dumps(data)
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {body}\n\n"


# ---------------------------------------------------------------- status

@router.get("/api/hub/status")
async def hub_status(request: Request) -> Dict[str, Any]:
    port = _hub_port()
    lan = _lan_ip()
    uptime_s = max(0.0, time.time() - OBS.started_at())
    return {
        "running": True,  # we ARE the hub — if you can read this, it's up
        "pid": os.getpid(),
        "port": port,
        "local_url": f"http://127.0.0.1:{port}",
        "lan_url": f"http://{lan}:{port}" if lan else "",
        "started_at": OBS.started_at(),
        "uptime_s": round(uptime_s, 1),
    }


# ---------------------------------------------------------------- control

def _delayed_shutdown(delay: float = 0.4) -> None:
    """Signal ourselves to exit after ``delay`` seconds, so the HTTP
    response can flush first. Uvicorn handles SIGINT/SIGTERM as a clean
    shutdown on both Windows and POSIX."""

    def _runner() -> None:
        time.sleep(delay)
        try:
            if sys.platform == "win32":
                # signal.raise_signal arrived in 3.8 and works under
                # uvicorn's SIGINT handler.
                signal.raise_signal(signal.SIGINT)
            else:
                os.kill(os.getpid(), signal.SIGTERM)
        except Exception as exc:  # noqa: BLE001 — fall back
            logger.error("⚠️ shutdown signal failed: %s — using os._exit", exc)
            os._exit(0)

    import threading
    threading.Thread(target=_runner, daemon=True).start()


def _restart_log_path() -> Path:
    """File the detached watchdog redirects the relaunched server into.

    The respawn is detached and outlives this process, so its stdout has
    nowhere to go in-process — and under ``pythonw`` there is no console
    at all. We give it a real file so (a) ``src.server``'s import-time
    logging write doesn't crash a console-less child, and (b) a failed
    restart leaves a diagnostic trail instead of vanishing silently.
    """
    return PROJECT_ROOT / "data" / "logs" / "restart.log"


def _spawn_respawn_watchdog() -> None:
    """Spawn a detached Python that waits for our PID to die then re-launches us.

    The relaunch is made the way ``src/server_process.start()`` spawns the
    hub — never a bare ``pythonw`` with no stdout. Two things matter:

    * **Executable.** When the hub is tray-launched ``sys.executable`` is
      ``pythonw.exe``. A console-less ``pythonw -m src.server`` dies on its
      first logging write at import, so normalise it to ``python.exe``.
    * **stdout/stderr.** Redirect the child into a log file (not a pipe —
      the watchdog exits right after spawning, leaving a pipe with no
      reader). Then health-check the port and record the outcome.
    """
    parent_pid = os.getpid()
    port = _hub_port()
    log_path = _restart_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("🔄 respawn watchdog: relaunch log → %s", log_path)
    script = (
        "import os, sys, time, socket, subprocess, datetime\n"
        f"parent={parent_pid}\n"
        f"port={port}\n"
        "log_path=" + repr(str(log_path)) + "\n"
        "root=" + repr(str(PROJECT_ROOT)) + "\n"
        "def alive(pid):\n"
        "    try:\n"
        "        if sys.platform == 'win32':\n"
        "            r = subprocess.run(['tasklist','/FI', f'PID eq {pid}'], capture_output=True, text=True)\n"
        "            return str(pid) in r.stdout\n"
        "        os.kill(pid, 0); return True\n"
        "    except OSError:\n"
        "        return False\n"
        "deadline = time.time() + 30\n"
        "while time.time() < deadline and alive(parent):\n"
        "    time.sleep(0.3)\n"
        "# wait briefly for the port to free\n"
        "for _ in range(60):\n"
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.settimeout(0.3)\n"
        "    try:\n"
        "        s.bind(('127.0.0.1', port)); s.close(); break\n"
        "    except OSError:\n"
        "        s.close(); time.sleep(0.3)\n"
        "# normalise pythonw.exe -> python.exe: a console-less child crashes\n"
        "# on src.server's import-time logging write.\n"
        "exe = sys.executable\n"
        "if exe.lower().endswith('pythonw.exe'):\n"
        "    cand = exe[:-len('pythonw.exe')] + 'python.exe'\n"
        "    if os.path.exists(cand):\n"
        "        exe = cand\n"
        "env = os.environ.copy()\n"
        "env['PYTHONIOENCODING'] = 'utf-8'\n"
        "env['PYTHONUTF8'] = '1'\n"
        "flags = 0\n"
        "if sys.platform == 'win32':\n"
        "    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW\n"
        "logf = open(log_path, 'a', encoding='utf-8', errors='replace')\n"
        "stamp = lambda: datetime.datetime.now().isoformat(timespec='seconds')\n"
        "logf.write(f'{stamp()} [respawn] relaunching {exe} -m src.server\\n'); logf.flush()\n"
        "child = subprocess.Popen([exe,'-m','src.server'], cwd=root, env=env,\n"
        "                         stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,\n"
        "                         creationflags=flags)\n"
        "ok = False\n"
        "for _ in range(100):  # ~30s\n"
        "    time.sleep(0.3)\n"
        "    if child.poll() is not None:\n"
        "        break\n"
        "    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    c.settimeout(0.3)\n"
        "    try:\n"
        "        c.connect(('127.0.0.1', port)); c.close(); ok = True; break\n"
        "    except OSError:\n"
        "        c.close()\n"
        "if ok:\n"
        "    logf.write(f'{stamp()} [respawn] hub back up on :{port} (pid={child.pid})\\n')\n"
        "else:\n"
        "    logf.write(f'{stamp()} [respawn] FAILED to bring hub up on :{port} (child rc={child.poll()})\\n')\n"
        "logf.flush()\n"
    )
    creationflags = 0
    if sys.platform == "win32":
        DETACHED = 0x00000008
        creationflags = DETACHED | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    # Capture the watchdog script's own stdout/stderr to the same log so a
    # failure *before* it opens its handle (e.g. a script-level exception)
    # is still visible rather than swallowed by DEVNULL.
    wd_log = open(log_path, "a", encoding="utf-8", errors="replace")
    subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=wd_log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )


@router.post("/api/hub/stop")
async def hub_stop() -> Dict[str, Any]:
    logger.info("🛑 /admin/api/hub/stop — scheduling self-shutdown")
    _delayed_shutdown()
    return {"ok": True, "detail": "hub will exit shortly"}


@router.post("/api/hub/restart")
async def hub_restart() -> Dict[str, Any]:
    logger.info("🔄 /admin/api/hub/restart — spawning respawn watchdog")
    # Tell the shutdown handler to leave the model backends running so the
    # respawned hub adopts them, instead of killing the survivors that
    # inherit_running_backends() exists to reclaim.
    from src import backend_process as bp

    bp.set_restart_pending(True)
    _spawn_respawn_watchdog()
    _delayed_shutdown(delay=0.8)
    return {"ok": True, "detail": "hub will restart shortly"}


# ----------------------------------------------------------------- log tail

@router.get("/api/hub/log/recent")
async def log_recent(limit: int = 400) -> Dict[str, Any]:
    return {"lines": HUB_LOG.lines(limit=max(1, min(limit, 2000)))}


@router.get("/api/hub/log/tail")
async def log_tail(request: Request) -> StreamingResponse:
    q = HUB_LOG.subscribe()
    seed = HUB_LOG.lines(limit=200)

    async def _gen() -> AsyncIterator[str]:
        try:
            for line in seed:
                yield _sse_pack(line)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    line = await asyncio.wait_for(q.get(), timeout=10.0)
                    yield _sse_pack(line)
                except asyncio.TimeoutError:
                    # Heartbeat keeps the connection (and any proxy) alive.
                    yield ":keepalive\n\n"
        finally:
            HUB_LOG.unsubscribe(q)

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


# ----------------------------------------------------------------- requests

@router.get("/api/hub/requests/recent")
async def requests_recent(limit: int = 50) -> Dict[str, Any]:
    return {"requests": OBS.recent_requests(limit=max(1, min(limit, 200)))}


@router.get("/api/hub/requests/stream")
async def requests_stream(request: Request) -> StreamingResponse:
    q = OBS.subscribe()
    seed = OBS.recent_requests(limit=20)

    async def _gen() -> AsyncIterator[str]:
        try:
            for rec in reversed(seed):  # send oldest-first so order matches stream
                yield _sse_pack(rec)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    rec = await asyncio.wait_for(q.get(), timeout=10.0)
                    from src.hub_observability import _rec_to_dict
                    yield _sse_pack(_rec_to_dict(rec))
                except asyncio.TimeoutError:
                    yield ":keepalive\n\n"
        finally:
            OBS.unsubscribe(q)

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@router.get("/api/hub/errors/recent")
async def errors_recent(limit: int = 50) -> Dict[str, Any]:
    return {"errors": OBS.recent_errors(limit=max(1, min(limit, 50)))}


@router.get("/api/hub/counters")
async def counters() -> Dict[str, Any]:
    return {"counters": OBS.counters_snapshot()}


# ----------------------------------------------------------------- stats

@router.get("/api/hub/stats")
async def stats() -> Dict[str, Any]:
    """gpu_stats() shells out to nvidia-smi (3s timeout). Keep it off the
    event loop so the rest of /admin stays snappy."""
    from src import system_stats

    ram = system_stats.ram_stats()
    gpus = await asyncio.to_thread(system_stats.gpu_stats)
    history = OBS.stats_snapshot()
    return {"ram": ram, "gpus": gpus, "history": history}


# ----------------------------------------------------------------- install

@router.get("/api/install/status")
async def install_status() -> Dict[str, Any]:
    """Run every install check off the event loop — many shell out to
    ``claude --version`` / ``nvidia-smi`` / ``llama-server --version``
    via blocking subprocess.run, which would otherwise pin the entire
    uvicorn worker for seconds while other admin requests queue up."""
    from src import install

    report = await asyncio.to_thread(install.run_all_checks)
    return {
        "worst_status": report.worst_status,
        "ok": report.ok,
        "checks": [asdict(c) for c in report.checks],
    }


@router.post("/api/install/fix")
async def install_fix(request: Request) -> Dict[str, Any]:
    """Run a single fix by ``fix_id``."""
    from src import install

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    fix_id = (body or {}).get("fix_id")
    if not fix_id:
        raise HTTPException(status_code=400, detail="fix_id is required")
    report = await asyncio.to_thread(install.run_all_checks)
    target = next((c for c in report.checks if c.fix_id == fix_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"no fixable check with fix_id={fix_id!r}")
    fn = install.fix_fn_for(target)
    if fn is None:
        raise HTTPException(status_code=400, detail=f"no fix function for {fix_id!r}")
    try:
        await asyncio.to_thread(fn)
    except Exception as exc:  # noqa: BLE001 — surface the failure to the UI
        raise HTTPException(status_code=500, detail=f"fix {fix_id!r} failed: {exc}")
    return {"ok": True, "fix_id": fix_id}


@router.post("/api/install/fix-all")
async def install_fix_all() -> Dict[str, Any]:
    from src import install

    report = await asyncio.to_thread(install.run_all_checks)
    ran: List[Dict[str, Any]] = []
    for c in report.checks:
        if c.status not in ("missing", "error"):
            continue
        fn = install.fix_fn_for(c)
        if fn is None:
            continue
        try:
            await asyncio.to_thread(fn)
            ran.append({"fix_id": c.fix_id, "ok": True})
        except Exception as exc:  # noqa: BLE001
            ran.append({"fix_id": c.fix_id, "ok": False, "error": str(exc)})
    return {"ran": ran}
