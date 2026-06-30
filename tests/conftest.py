"""Shared test configuration.

Disables OpenTelemetry SDK in unit tests so we don't:
  * try to open a gRPC connection to a non-existent OTLP endpoint
  * log "OTel initialised" / "OTLP export failed" lines that pollute test output
  * leak background BatchSpanProcessor threads between test sessions

The trace_id middleware + GenAI helpers are exercised independently via
their own unit tests against the disabled-mode no-ops.
"""

from __future__ import annotations

import os

os.environ.setdefault("OTEL_SDK_DISABLED", "true")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_shared_http_clients():
    """Reset the hub's shared httpx client singletons around every test.

    The hub reuses one pooled ``httpx.AsyncClient`` / ``httpx.Client`` across
    requests (issue #165) and caches it module-side. Tests that monkeypatch
    ``httpx.AsyncClient`` / ``httpx.Client`` to a fake need the cache cleared so
    ``get_async_client()`` / ``get_sync_client()`` reconstruct the patched class
    fresh, and so a real client built in one test never leaks into the next.
    """
    from src import http_client

    http_client._async_client = None
    http_client._sync_client = None
    yield
    http_client._async_client = None
    http_client._sync_client = None
