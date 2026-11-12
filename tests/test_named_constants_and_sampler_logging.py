"""Named-constant and silent-failure guards for the Playground proxy, the
Copilot billing rate and the resource sampler.

A bare timeout or rate literal cannot be reasoned about by the next reader,
and a sampler that swallows its own failure turns a breakage into a flat
sparkline with nothing in the log.
"""

from __future__ import annotations

import ast
import asyncio
import logging
from datetime import date
from pathlib import Path

import pytest

from src import server_lifecycle
from src.usage import copilot_billing

_PLAYGROUND = Path(__file__).resolve().parents[1] / "app_web" / "routers" / "playground.py"


def test_playground_timeouts_are_named_constants() -> None:
    """Every ``timeout=`` in the Playground router is a name, never a number."""
    tree = ast.parse(_PLAYGROUND.read_text(encoding="utf-8"))
    bare = [
        node.value.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == "timeout"
        and isinstance(node.value, ast.Constant)
    ]
    assert bare == [], f"bare timeout literal(s) at playground.py lines {bare}"


def test_billing_usd_uses_named_rate() -> None:
    rows = copilot_billing._aggregate_day(
        date(2026, 1, 1), [{"model": "m", "netAmount": 250}])
    assert rows == [{
        "date": "2026-01-01", "model": "m", "credits": 250.0,
        "usd": 250.0 * copilot_billing._USD_PER_CREDIT,
    }]
    assert copilot_billing._USD_PER_CREDIT == pytest.approx(0.01)


def test_resource_sampler_logs_each_distinct_failure_once(
    monkeypatch, caplog
) -> None:
    from src import system_stats

    outcomes = iter([
        RuntimeError("boom"), RuntimeError("boom"), ValueError("other"),
    ])

    def _ram():
        out = next(outcomes, None)
        if out is None:
            raise asyncio.CancelledError
        raise out

    monkeypatch.setattr(system_stats, "ram_stats", _ram)
    monkeypatch.setattr(system_stats, "cpu_stats", lambda: {})
    monkeypatch.setattr(system_stats, "gpu_stats", lambda: [])
    monkeypatch.setattr(server_lifecycle, "_RESOURCE_SAMPLE_INTERVAL_S", 0)

    with caplog.at_level(logging.WARNING, logger=server_lifecycle.logger.name):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(server_lifecycle._resource_sampler())

    messages = [r.getMessage() for r in caplog.records if "sampler" in r.getMessage()]
    assert len(messages) == 2, messages
    assert "RuntimeError: boom" in messages[0]
    assert "ValueError: other" in messages[1]
