"""Throwaway hub for the design-review walk's synthetic run (#736).

``/design-review local-llm-hub --synthetic`` runs this (``[design.review.synthetic]``
in ``.fleet.toml``) with the repo's venv python, from the repo root. It boots the
e2e suite's isolated hub (``tests/e2e/_isolated_hub.py``: no live services, no
peers, an egress guard) on a free loopback port with ``--seed-demo``, so the
request and error lists hold invented records instead of the live hub's
traffic. It prints ``URL=<base>`` once the hub answers, then waits until stdin
closes and tears the hub (and anything it spawned) down.

Never points at the live hub on :8000 and never writes anything outside its own
temporary state directory.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import win_job  # noqa: E402
from src.no_window import NO_WINDOW  # noqa: E402

logger = logging.getLogger("design_review_synthetic")
STARTUP_TIMEOUT_S = 60.0


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    state_dir = Path(tempfile.mkdtemp(prefix="llmhub-synthetic-"))
    log_fp = (state_dir / "hub.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.e2e._isolated_hub", "--port", str(port),
         "--state-dir", str(state_dir), "--seed-demo"],
        cwd=str(ROOT), stdout=log_fp, stderr=subprocess.STDOUT,
        creationflags=NO_WINDOW,
    )
    job = win_job.create_kill_on_close_job(f"local-llm-hub-synthetic-{port}")
    if job:
        win_job.assign_pid(job, proc.pid)
    try:
        deadline = time.time() + STARTUP_TIMEOUT_S
        while True:
            if proc.poll() is not None:
                logger.error("❌ synthetic hub exited early; log: %s", state_dir / "hub.log")
                return 1
            try:
                if httpx.get(f"{base}/health", timeout=1.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.time() > deadline:
                logger.error("❌ synthetic hub not reachable after %ss", STARTUP_TIMEOUT_S)
                return 1
            time.sleep(0.3)
        logger.info("✅ synthetic hub up on %s (state %s)", base, state_dir)
        print(f"URL={base}", flush=True)
        # The walk closes our stdin when it is done; reading to EOF blocks
        # until then.
        sys.stdin.read()
        return 0
    finally:
        if job:
            win_job.terminate_and_close(job)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_fp.close()


if __name__ == "__main__":
    sys.exit(main())
