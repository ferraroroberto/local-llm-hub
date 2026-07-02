"""SSH-triggered remote hub bootstrap/sync (#181).

Brings a *dead* remote host's hub back up, or syncs it to the latest
``main`` + restarts — both over the same forced-command-restricted SSH
channel (``mac/bin/hub-remote-ctl.sh``), never a general shell. The verb
sent over SSH (``bootstrap`` / ``sync``) becomes ``$SSH_ORIGINAL_COMMAND``
on the remote end; the forced command there — not anything sent here —
decides what actually runs.

Symmetric by construction: which host can dial which is entirely a
function of ``HostProfile.ssh_user``/``address`` (config) and whether
``LOCAL_LLM_HUB_SSH_KEY`` is set in this process's ``.env`` (today, only on
``pc-cuda``) — nothing here hardcodes which host is the caller.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from typing import Any, Dict, Optional

import httpx

from .host_profile import get_host, hub_port

logger = logging.getLogger(__name__)

_SSH_KEY_ENV = "LOCAL_LLM_HUB_SSH_KEY"
_SSH_CONNECT_TIMEOUT_S = 5
_HEALTH_POLL_TIMEOUT_S = 30
_HEALTH_POLL_INTERVAL_S = 1.0


def _ssh_key_path() -> Optional[str]:
    return os.environ.get(_SSH_KEY_ENV)


def _run_remote_command(host_id: str, verb: str) -> Dict[str, Any]:
    key_path = _ssh_key_path()
    if not key_path:
        return {"ok": False, "error": f"{_SSH_KEY_ENV} is not set in .env"}
    owner = get_host(host_id)
    if owner is None or not owner.address or not owner.ssh_user:
        return {"ok": False, "error": f"host {host_id!r} has no address/ssh_user configured"}
    cmd = [
        "ssh", "-i", key_path,
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={_SSH_CONNECT_TIMEOUT_S}",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{owner.ssh_user}@{owner.address}",
        verb,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_SSH_CONNECT_TIMEOUT_S + 10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        return {"ok": False, "error": f"ssh exit {result.returncode}: {(result.stderr or '').strip()}"}
    return {"ok": True}


async def _poll_health(host_id: str) -> Dict[str, Any]:
    owner = get_host(host_id)
    if owner is None or not owner.address:
        return {"reachable": False, "error": f"host {host_id!r} has no address configured"}
    base = f"http://{owner.address}:{hub_port()}"
    deadline = time.monotonic() + _HEALTH_POLL_TIMEOUT_S
    last_error = ""
    async with httpx.AsyncClient(timeout=3.0) as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(f"{base}/health")
                if r.status_code < 500:
                    return {"reachable": True, "address": base}
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(_HEALTH_POLL_INTERVAL_S)
    return {"reachable": False, "address": base, "error": last_error or "timed out"}


async def bootstrap_host(host_id: str) -> Dict[str, Any]:
    """Trigger the remote host's ``bootstrap`` action, then poll ``/health``
    for up to ~30s. ``ok`` is only true once the peer actually answers."""
    logger.info("🛎️ bootstrap_host(%r)", host_id)
    ssh_result = await asyncio.to_thread(_run_remote_command, host_id, "bootstrap")
    if not ssh_result["ok"]:
        return {"ok": False, "detail": ssh_result["error"]}
    health = await _poll_health(host_id)
    return {"ok": health["reachable"], "detail": health}


async def sync_host(host_id: str) -> Dict[str, Any]:
    """Trigger the remote host's ``sync`` action (git pull + restart), then
    poll ``/health`` for up to ~30s."""
    logger.info("🔃 sync_host(%r)", host_id)
    ssh_result = await asyncio.to_thread(_run_remote_command, host_id, "sync")
    if not ssh_result["ok"]:
        return {"ok": False, "detail": ssh_result["error"]}
    health = await _poll_health(host_id)
    return {"ok": health["reachable"], "detail": health}
