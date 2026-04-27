"""End-to-end smoke test — requires a running hub (and live backends for local models).

Usage (in two terminals):

    # Terminal 1
    .venv\\Scripts\\python -m src.run_backend hub

    # Terminal 2
    .venv\\Scripts\\python scripts/smoke_test.py

Loops over every model enabled for this host:

  - claude-* via raw /v1/messages and the anthropic SDK (proves drop-in fidelity).
  - local openai-backed models via raw /v1/messages (routed through the hub).

Any model whose backend is not reachable is skipped with a warning rather
than failed — this is a smoke test, not a coverage gate.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from anthropic import Anthropic  # noqa: E402

from src import backend_process as lp  # noqa: E402
from src.model_registry import enabled_models  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"


def via_raw_http(model_name: str) -> str:
    log.info("-> POST /v1/messages  model=%s", model_name)
    r = httpx.post(
        f"{BASE_URL}/v1/messages",
        json={
            "model": model_name,
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        },
        timeout=300.0,
    )
    r.raise_for_status()
    body = r.json()
    text = body["content"][0]["text"] if body.get("content") else ""
    log.info("   model=%s  usage=%s", body.get('model'), body.get('usage'))
    log.info("   text=%r", text)
    return text


def via_sdk(model_name: str) -> str:
    log.info("-> anthropic SDK  model=%s", model_name)
    client = Anthropic(api_key="local-dummy", base_url=BASE_URL)
    msg = client.messages.create(
        model=model_name,
        max_tokens=64,
        system="Answer in one word.",
        messages=[{"role": "user", "content": "Capital of France?"}],
    )
    text = msg.content[0].text if msg.content else ""
    log.info("   model=%s  in:%s out:%s", msg.model, msg.usage.input_tokens, msg.usage.output_tokens)
    log.info("   text=%r", text)
    return text


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        httpx.get(f"{BASE_URL}/health", timeout=5.0).raise_for_status()
    except Exception as e:
        log.error("hub not reachable at %s: %s", BASE_URL, e)
        log.error("start it with: .venv\\Scripts\\python -m src.run_backend hub")
        return 1

    failures: list[str] = []
    skipped: list[str] = []
    passed: list[str] = []

    for m in enabled_models():
        if m.backend == "whisper":
            log.info("[skip] %s — audio backend, not a chat model", m.display_name)
            skipped.append(m.display_name)
            continue
        if m.backend == "openai" and not lp.is_reachable(m):
            log.info("[skip] %s — backend on :%s not reachable", m.display_name, m.port)
            skipped.append(m.display_name)
            continue
        log.info("\n=== %s (%s) ===", m.display_name, m.backend)
        try:
            t = via_raw_http(m.display_name)
            if not t:
                failures.append(f"{m.display_name}: empty response")
                continue
            if m.backend == "claude":
                t2 = via_sdk(m.display_name)
                if not t2:
                    failures.append(f"{m.display_name} (sdk): empty response")
                    continue
            passed.append(m.display_name)
        except Exception as e:
            failures.append(f"{m.display_name}: {e}")

    log.info("\n---- summary ----")
    log.info("  passed : %d — %s", len(passed), ', '.join(passed) or '(none)')
    log.info("  skipped: %d — %s", len(skipped), ', '.join(skipped) or '(none)')
    log.info("  failed : %d", len(failures))
    for f in failures:
        log.info("    - %s", f)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
