"""Selector event-loop shim (issue #222) -- root cause of hub-unresponsive wedges.

asyncio's default Windows proactor event loop closes its listening socket
on any aborted client connection (WinError 64); the selector loop's accept
path doesn't. These tests cover the wiring (all 4 of this repo's
``uvicorn.run()`` call sites pick the shim) and the actual accept-loop
resilience the shim buys.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
from pathlib import Path

import pytest

from src.event_loop import LOOP_FACTORY, selector_loop_factory

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_selector_loop_factory_returns_selector_instance_on_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    sentinel = object()
    monkeypatch.setattr(asyncio, "SelectorEventLoop", lambda: sentinel)
    assert selector_loop_factory() is sentinel


def test_selector_loop_factory_defers_on_other_platforms(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    sentinel = object()
    monkeypatch.setattr(asyncio, "new_event_loop", lambda: sentinel)
    assert selector_loop_factory() is sentinel


def test_selector_loop_factory_is_zero_arg_and_returns_an_instance():
    """Regression pin: uvicorn imports a *custom* loop= target and calls
    it as a bare Callable[[], AbstractEventLoop] -- no use_subprocess kwarg,
    and it must return an instantiated loop, not a loop class (app-launcher
    #388's original bug: returning the class left Runner calling unbound
    methods)."""
    loop = selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop)
    finally:
        loop.close()


def test_loop_factory_dotted_path_matches_module():
    """LOOP_FACTORY is a dotted-path string uvicorn imports fresh via
    importlib -- it must resolve to this module regardless of how the
    servers import the constant themselves."""
    assert LOOP_FACTORY == "src.event_loop:selector_loop_factory"


def _wires_loop_factory(relpath: str) -> None:
    src = (_REPO_ROOT / relpath).read_text(encoding="utf-8")
    assert "event_loop import LOOP_FACTORY" in src, f"{relpath} doesn't import LOOP_FACTORY"
    assert "loop=LOOP_FACTORY" in src, f"{relpath} doesn't pass loop=LOOP_FACTORY to uvicorn.run"


def test_hub_server_wires_loop_factory():
    _wires_loop_factory("src/server.py")


def test_whisper_translate_proxy_wires_loop_factory():
    _wires_loop_factory("src/whisper_translate_proxy.py")


def test_tts_server_wires_loop_factory():
    _wires_loop_factory("src/tts_server.py")


def test_parakeet_server_wires_loop_factory():
    _wires_loop_factory("src/parakeet_server.py")


async def _noop_handler(reader, writer):
    writer.close()


def _abort_connect_sync(port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
    try:
        s.settimeout(0.5)
        s.connect(("127.0.0.1", port))
    except OSError:
        pass
    finally:
        s.close()  # SO_LINGER(1, 0) forces an RST instead of a FIN


async def _still_accepting(port: int) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=1.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def _bombard_with_aborts(rounds: int, burst: int) -> None:
    server = await asyncio.start_server(_noop_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        for _ in range(rounds):
            threads = [
                threading.Thread(target=_abort_connect_sync, args=(port,))
                for _ in range(burst)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            await asyncio.sleep(0.02)
            assert await _still_accepting(
                port
            ), "listener died on an aborted client connection (issue #222)"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.skipif(sys.platform != "win32", reason="proactor-loop bug is Windows-only")
def test_selector_loop_survives_aborted_connections():
    asyncio.run(_bombard_with_aborts(rounds=10, burst=20), loop_factory=asyncio.SelectorEventLoop)


@pytest.mark.skipif(sys.platform != "win32", reason="proactor-loop bug is Windows-only")
def test_proactor_loop_dies_on_aborted_connections():
    """Documents the bug this issue fixes -- the shim exists because this
    fails. If a future CPython/uvicorn release fixes the proactor loop
    itself, this test (not the shim) is what should be revisited."""
    with pytest.raises(AssertionError):
        asyncio.run(
            _bombard_with_aborts(rounds=10, burst=20), loop_factory=asyncio.ProactorEventLoop
        )
