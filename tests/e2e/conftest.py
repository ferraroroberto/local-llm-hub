"""Boot a hub instance on a free port and expose its URL as a fixture.

Each e2e session spawns one ``uvicorn src.server:app`` on a random free
port and tears it down at the end. The hub is a single ASGI process —
the /admin SPA, the routers, and the /v1 surface all share one
event loop, so a single boot covers every endpoint.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


@pytest.fixture(scope="session")
def hub_url() -> Iterator[str]:
    port = _free_tcp_port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # The autostart sampler hits nvidia-smi every 2s. On a CI runner
    # without an NVIDIA GPU that's noisy but harmless.
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    log_path = PROJECT_ROOT / "tests" / "e2e" / "autoboot-hub.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fp = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(PROJECT_ROOT),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=creationflags,
    )

    deadline = time.time() + 30.0
    last_err = "timed out"
    while time.time() < deadline:
        if proc.poll() is not None:
            log_fp.close()
            tail = log_path.read_text(encoding="utf-8")[-1500:]
            pytest.fail(f"hub exited before becoming reachable. Log tail:\n{tail}")
        try:
            r = httpx.get(f"{url}/health", timeout=1.0)
            if r.status_code == 200:
                break
        except httpx.HTTPError as exc:
            last_err = repr(exc)
        time.sleep(0.3)
    else:
        proc.terminate()
        log_fp.close()
        pytest.fail(f"hub never became reachable: {last_err}")

    try:
        yield url
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_fp.close()


@pytest.fixture(scope="session")
def admin_url(hub_url: str) -> str:
    return f"{hub_url}/admin/"
