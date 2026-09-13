"""Launch the e2e test hub cut off from this machine's live services (#592).

``tests/e2e/conftest.py`` runs this as ``python -m tests.e2e._isolated_hub``
in place of a bare ``uvicorn src.server:app``. A bare hub is the production
app with production config: every admin page load probed the live hub on
``:8000`` and whisper on ``:8090``, spawned ``claude`` / ``agy`` /
``nvidia-smi`` / ``llama-server`` / ``whisper-server`` / ``docker``, and its
Machines / placement endpoints SSH'd and TCP-probed the real LAN peers. So a
run's result depended on those services, and it loaded them.

Two layers, both installed before ``src.server`` is imported:

* **Isolation** (:func:`isolate_from_live_services`) — the hub reads the
  pinned committed config with no ``machines.local.yaml`` beside it (so no
  peer has an address to dial), starts none of its background control loops,
  and gets a fixed install report and inert GPU / Docker / LAN-IP / backend
  probes.
* **Egress guard** (:func:`install_egress_guard`) — an audit hook that
  refuses, and records, every socket connect or send to anything but a
  loopback socket this process bound itself (its listener, the event loop's
  self-pipe) and every process spawn but ``git``. The hook fires
  before the OS call, so a regressed isolation stub is refused rather than
  reaching a live service; ``conftest.py`` fails the test that caused it.
  :func:`_prove_guard_armed` refuses a canary of each kind at boot, so a
  guard that silently stopped firing cannot pass as a quiet run.

The hub runs on ``src.event_loop``'s selector loop, as ``src.server.main()``
does: every TCP connect then goes through ``socket.connect``, which is what
the audit hook sees.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import json
import os
import socket
import subprocess
import sys
import threading
import traceback
import weakref
from pathlib import Path
from typing import Any, List, Optional

# Spawned for build identity (``/admin/api/version``'s git sha and config
# sha). Local and read-only — not a service.
ALLOWED_EXECUTABLES = frozenset({"git"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
VIOLATIONS_FILE = "egress-violations.jsonl"

_CANARY_LAN = ("192.0.2.1", 9)  # TEST-NET-1: reserved, never routed
_CANARY_LOOPBACK = ("127.0.0.1", 9)  # discard port — nothing of ours binds it
_CANARY_EXECUTABLE = "e2e-egress-guard-canary"

_guard_lock = threading.Lock()
_guard_state = threading.local()


def _project_frames() -> List[str]:
    """The innermost repo frames of the refused call — enough to name the
    code path in a failure message. Empty for a connect made inside an
    anyio connect subtask (async httpx) — the target address still names it."""
    frames = []
    for fr in traceback.extract_stack()[:-3]:
        path = fr.filename.replace("\\", "/")
        for root in ("/src/", "/app_web/"):
            if root in path:
                frames.append(f"{root.strip('/')}/{path.split(root, 1)[1]}:{fr.lineno} {fr.name}")
    return frames[-6:]


def _executable_name(executable: Any, args: Any) -> str:
    """``git`` for ``git``, ``C:\\...\\git.EXE`` or ``"git log"``. A quoted
    path with spaces yields a mangled name — refused, which fails closed."""
    if executable is not None:
        target = os.fsdecode(executable)
    elif isinstance(args, (str, bytes)):
        target = os.fsdecode(args).split(" ", 1)[0]
    else:
        target = os.fsdecode(list(args)[0]) if args else ""
    return Path(target.strip('"')).stem.lower()


def install_egress_guard(violations_path: Path) -> None:
    """Refuse and record every egress but to this process's own loopback
    sockets, and every spawn but ``git``."""
    # Loopback sockets this process bound: the hub's listener, and the
    # listener half of the selector loop's self-pipe ``socketpair()``.
    own_sockets: "weakref.WeakSet[socket.socket]" = weakref.WeakSet()

    def refuse(kind: str, target: str, exc: OSError) -> None:
        if not getattr(_guard_state, "canary", False):
            record = {"kind": kind, "target": target, "stack": _project_frames()}
            with _guard_lock, violations_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record) + "\n")
        raise exc

    def own_port(port: Any) -> bool:
        for sock in list(own_sockets):
            try:
                if sock.getsockname()[1] == port:
                    return True
            except OSError:  # closed since
                continue
        return False

    def allowed(address: Any) -> bool:
        return (
            isinstance(address, tuple)
            and len(address) >= 2
            and str(address[0]).lower() in LOOPBACK_HOSTS
            and own_port(address[1])
        )

    def hook(event: str, args: tuple) -> None:
        # Writing the record opens a file, which is itself an audit event.
        if getattr(_guard_state, "busy", False):
            return
        _guard_state.busy = True
        try:
            if event == "socket.bind":
                sock, address = args
                if isinstance(address, tuple) and str(address[0]).lower() in LOOPBACK_HOSTS:
                    own_sockets.add(sock)
            elif event in ("socket.connect", "socket.sendto"):
                address = args[1]
                if not allowed(address):
                    refuse(
                        event, repr(address),
                        ConnectionRefusedError(
                            errno.ECONNREFUSED,
                            f"e2e egress guard refused {event} to {address!r} (#592)",
                        ),
                    )
            elif event == "subprocess.Popen":
                name = _executable_name(args[0], args[1])
                if name not in ALLOWED_EXECUTABLES:
                    refuse(
                        event, name,
                        PermissionError(errno.EACCES, f"e2e egress guard refused spawning {name!r} (#592)"),
                    )
            elif event in ("os.system", "os.startfile", "os.spawn", "os.exec", "os.posix_spawn"):
                refuse(
                    event, repr(args)[:200],
                    PermissionError(errno.EACCES, f"e2e egress guard refused {event} (#592)"),
                )
        finally:
            _guard_state.busy = False

    sys.addaudithook(hook)


def _prove_guard_armed() -> None:
    """Refuse a LAN connect, a loopback connect to a port this process does
    not own, and a spawn — on purpose, unrecorded. Exits the process if any
    gets through to the OS; ``conftest.py`` then fails the boot with this
    message instead of running an unguarded suite."""
    _guard_state.canary = True
    try:
        for label, attempt, refusal in (
            ("LAN connect", lambda: socket.create_connection(_CANARY_LAN, timeout=0.1), ConnectionRefusedError),
            ("loopback connect", lambda: socket.create_connection(_CANARY_LOOPBACK, timeout=0.1), ConnectionRefusedError),
            ("spawn", lambda: subprocess.Popen([_CANARY_EXECUTABLE]), PermissionError),
        ):
            try:
                attempt()
            except refusal as exc:
                if "e2e egress guard" in str(exc):
                    continue
                raise SystemExit(f"e2e egress guard is not armed: canary {label} failed on its own: {exc!r}")
            except OSError as exc:
                raise SystemExit(f"e2e egress guard is not armed: canary {label} reached the OS: {exc!r}")
            raise SystemExit(f"e2e egress guard is not armed: canary {label} succeeded")
    finally:
        _guard_state.canary = False


def _pin_config(state_dir: Path) -> None:
    """Point the hub at the pinned committed config, alone in ``state_dir``.

    Same copy the unit suite reads (``tests/conftest.py``, #565): placement
    edits on ``main`` cannot change it, and with no identity overlay beside it
    no peer host has an address, so nothing can dial the LAN. Must run before
    any module that binds ``CONFIG_PATH`` at import (``src.config_write``).
    """
    from src import host_profile
    from tests._placement_fixture import committed_config_path, pin_placement_text

    pinned = state_dir / "models.yaml"
    pinned.write_text(
        pin_placement_text(committed_config_path().read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    host_profile.CONFIG_PATH = pinned
    host_profile._CONFIG_CACHE.clear()


def _stub(module: Any, name: str, replacement: Any) -> None:
    """``setattr`` that fails the boot if the target was renamed away — a
    stub that silently patched nothing would only surface as guard refusals."""
    if not hasattr(module, name):
        raise SystemExit(f"e2e isolation: {module.__name__}.{name} no longer exists")
    setattr(module, name, replacement)


def isolate_from_live_services(state_dir: Path) -> None:
    """Make the hub's live-machine probes inert and deterministic.

    Patched on the defining modules before ``src.server`` imports the
    routers, so ``from src.x import y`` bindings pick up the stubs too.
    """
    # Langfuse (Docker, :3000) and AgentsView (:8080) are live local
    # services. An empty base URL disables AgentsView (as tests/conftest.py
    # does) and fails every Langfuse call before a socket opens; OTel export
    # targets Langfuse too. Set before ``src.server`` loads ``.env``, which
    # never overrides a variable already present.
    os.environ["AGENTSVIEW_BASE_URL"] = ""
    os.environ["LANGFUSE_HOST"] = ""
    os.environ["OTEL_SDK_DISABLED"] = "true"
    _pin_config(state_dir)

    from src import backend_process, install, server_lifecycle, server_process, services, system_stats
    from src.hub_log import HUB_LOG
    from src.hub_observability import OBS

    async def wire_loops_only() -> None:
        # The real startup also adopts and autostarts backends, launches
        # Docker/Langfuse/AgentsView, and runs the fleet reconcile, model
        # failover, config-drift and idle-unload loops — every one of them
        # reaches live processes or peers. The SPA needs only the SSE loop
        # wiring and the stats ring.
        loop = asyncio.get_running_loop()
        OBS.attach_loop(loop)
        HUB_LOG.attach_loop(loop)
        server_lifecycle._BACKGROUND_TASKS.append(loop.create_task(server_lifecycle._resource_sampler()))

    fixed_report = install.Report(checks=[
        install.Check("e2e", "Install checks", "ok", "fixed report in the e2e test hub (#592)"),
    ])

    def run_all_checks(*, use_cache: bool = False) -> install.Report:
        return fixed_report

    def docker_info(timeout_s: float) -> dict:
        return {"running": False, "error": "docker probe disabled in the e2e test hub"}

    def no_lan_ip() -> Optional[str]:
        return None

    _stub(server_lifecycle, "wire_observatory_loop", wire_loops_only)
    _stub(install, "run_all_checks", run_all_checks)
    _stub(system_stats, "gpu_stats", lambda: [])
    _stub(server_process, "lan_ip", no_lan_ip)
    _stub(services, "_docker_info_sync", docker_info)
    _stub(backend_process, "is_reachable", lambda model, timeout=1.5: False)
    _stub(backend_process, "probe_health", lambda model, timeout=1.5: None)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    install_egress_guard(args.state_dir / VIOLATIONS_FILE)
    _prove_guard_armed()
    isolate_from_live_services(args.state_dir)

    import uvicorn

    from src.event_loop import LOOP_FACTORY

    uvicorn.run(
        "src.server:app", host="127.0.0.1", port=args.port,
        log_level="warning", reload=False, loop=LOOP_FACTORY,
    )


if __name__ == "__main__":
    main()
