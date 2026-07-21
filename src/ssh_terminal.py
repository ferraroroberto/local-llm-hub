"""In-browser SSH terminal via app-launcher's session-host (#309, Step 3).

We deliberately **reuse** app-launcher's PTY/xterm engine rather than
rebuilding a ConPTY/WebSocket/xterm stack in the hub. app-launcher already
runs its session-host as a standalone loopback HTTP+WS service
(``python -m app.session_host.server``, default ``127.0.0.1:8446``) whose
``PtySession`` can spawn any ``cmd /c <exe> <flags>`` and stream it over a
WebSocket. The hub creates an ``ssh <user>@<host>`` session there and proxies
the WebSocket to the browser (the pump lives in the router, which owns the
client WebSocket); the hub applies its own auth via the existing middleware.

**Cross-repo dependency (the companion issue).** The session-host gates the
spawned command through app-launcher's ``src/agents.py::AGENTS`` registry —
``POST /sessions`` 400s on an unregistered ``agent`` and ``create()`` runs
``command_for(agent)``, so it cannot run an arbitrary ``ssh`` today. Until
app-launcher registers an ``ssh`` agent (command ``ssh``, caller-supplied
``<user>@<host>`` flags), :func:`create_ssh_session` fails cleanly and the
tab shows the terminal as unavailable with an actionable reason. This is a
real dependency, tracked by a linked app-launcher issue — not an assumption.

Windows-only, which is fine: the session-host is ConPTY-based and the hub
host (tower) is Windows.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

import httpx

from src.host_profile import HostProfile

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Same env var + default app-launcher's own webapp uses to find the
# session-host, so the two stay pointed at the same loopback service.
SESSION_HOST_PORT_ENV = "LAUNCHER_SESSION_HOST_PORT"
DEFAULT_SESSION_HOST_PORT = 8446

# The app-launcher agent id the companion issue must register. Kept as one
# named constant so the create call and the not-available reason agree.
SSH_AGENT_ID = "ssh"

_PROBE_TIMEOUT_S = 2.0
_CREATE_TIMEOUT_S = 8.0


def session_host_base() -> str:
    """Loopback base URL of app-launcher's session-host."""
    port = os.environ.get(SESSION_HOST_PORT_ENV) or DEFAULT_SESSION_HOST_PORT
    return f"http://127.0.0.1:{port}"


def session_host_ws_url(session_id: str) -> str:
    """Upstream WebSocket URL for a session's stream (role=phone so the
    session-host honours our resize frames — role=pc is the mirror side)."""
    port = os.environ.get(SESSION_HOST_PORT_ENV) or DEFAULT_SESSION_HOST_PORT
    return f"ws://127.0.0.1:{port}/sessions/{session_id}/ws?role=phone"


async def terminal_status() -> Dict[str, Any]:
    """Is the in-browser SSH terminal available on this host?

    Probes the session-host ``/healthz``. Returns
    ``{available, reason, session_host}``. ``available`` only means the
    engine is reachable — the ``ssh`` agent may still be unregistered on
    app-launcher's side, which surfaces at create time with an actionable
    error (see the module docstring)."""
    base = session_host_base()
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            r = await client.get(f"{base}/healthz")
        if r.status_code < 500:
            return {"available": True, "reason": "", "session_host": base}
        return {"available": False, "reason": f"session-host HTTP {r.status_code}", "session_host": base}
    except Exception as exc:  # noqa: BLE001 — network / connection
        logger.debug("session-host probe failed: %s", exc)
        return {
            "available": False,
            "reason": "app-launcher session-host not reachable on this host",
            "session_host": base,
        }


async def create_ssh_session(
    host: HostProfile, *, cols: int = 120, rows: int = 30
) -> Dict[str, Any]:
    """Create an ``ssh <user>@<host>`` PTY session on the session-host.

    Returns ``{ok, session_id, error}``. A 400 from the session-host almost
    always means app-launcher hasn't registered the ``ssh`` agent yet — the
    error is worded to point at the companion issue rather than leaking the
    upstream detail."""
    if not host.can_ssh:
        return {"ok": False, "session_id": None, "error": "host has no SSH target configured"}
    target = f"{host.ssh_user}@{host.address}"
    payload = {
        "kind": "pty",
        "agent": SSH_AGENT_ID,
        "flags": target,
        "project_dir": str(PROJECT_ROOT),
        "name": f"ssh {host.display_name or host.id}",
        "label": host.display_name or host.id,
        "cols": cols,
        "rows": rows,
    }
    base = session_host_base()
    try:
        async with httpx.AsyncClient(timeout=_CREATE_TIMEOUT_S) as client:
            r = await client.post(f"{base}/sessions", json=payload)
    except Exception as exc:  # noqa: BLE001 — network / connection
        logger.warning("⚠️ ssh session create failed: %s", exc)
        return {"ok": False, "session_id": None, "error": "session-host not reachable"}
    if r.status_code == 400:
        return {
            "ok": False,
            "session_id": None,
            "error": (
                "app-launcher has no 'ssh' agent registered yet — the "
                "in-browser SSH terminal needs the linked app-launcher "
                "companion change before it can open"
            ),
        }
    if r.status_code >= 400:
        return {"ok": False, "session_id": None, "error": f"session-host HTTP {r.status_code}"}
    try:
        body = r.json()
        sid = body.get("session_id") or body.get("id")
    except Exception:  # noqa: BLE001 — bad JSON
        sid = None
    if not sid:
        return {"ok": False, "session_id": None, "error": "session-host returned no session id"}
    return {"ok": True, "session_id": sid, "error": ""}
