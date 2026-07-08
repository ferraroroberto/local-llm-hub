"""Unit tests for src/claude_code_otel.py (issue #68).

Builds real ExportMetricsServiceRequest protobuf messages (rather than
replaying opaque captured bytes) so the test doc­uments the exact wire shape
being relied on: Sum metrics, DELTA temporality, the attribute set Claude
Code actually sends (verified against a real capture during planning).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import claude_code_otel as cco


def _build_export(data_points):
    """Build a minimal ExportMetricsServiceRequest with one scope containing
    a claude_code.token.usage and/or claude_code.cost.usage Sum metric.

    ``data_points`` is a list of (metric_name, value, attrs) tuples.
    """
    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
    from opentelemetry.proto.metrics.v1 import metrics_pb2

    req = metrics_service_pb2.ExportMetricsServiceRequest()
    rm = req.resource_metrics.add()
    sm = rm.scope_metrics.add()

    by_name = {}
    for name, value, attrs in data_points:
        by_name.setdefault(name, []).append((value, attrs))

    for name, points in by_name.items():
        metric = sm.metrics.add()
        metric.name = name
        metric.sum.aggregation_temporality = (
            metrics_pb2.AGGREGATION_TEMPORALITY_DELTA
        )
        metric.sum.is_monotonic = True
        for value, attrs in points:
            dp = metric.sum.data_points.add()
            dp.as_double = value
            dp.time_unix_nano = 1_700_000_000_000_000_000
            for k, v in attrs.items():
                a = dp.attributes.add()
                a.key = k
                a.value.string_value = v

    return req.SerializeToString()


@pytest.fixture(autouse=True)
def _isolate_data_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cco, "_DATA_DIR", tmp_path / "telemetry")
    monkeypatch.setattr(cco, "_DATA_FILE", tmp_path / "telemetry" / "usage.jsonl")
    cco._reset_for_tests()
    yield
    cco._reset_for_tests()


def test_parse_export_request_extracts_token_and_cost():
    raw = _build_export(
        [
            (
                "claude_code.token.usage",
                525.0,
                {"model": "claude-haiku-4-5-20251001", "query_source": "auxiliary", "type": "input",
                 "user.email": "roberto.ferraro@gmail.com", "session.id": "abc-123"},
            ),
            (
                "claude_code.cost.usage",
                0.0006,
                {"model": "claude-haiku-4-5-20251001", "query_source": "auxiliary",
                 "user.account_id": "user_01N68"},
            ),
        ]
    )
    points = cco.parse_export_request(raw)
    assert len(points) == 2

    token_point = next(p for p in points if p.metric == "token")
    assert token_point.model == "claude-haiku-4-5-20251001"
    assert token_point.query_source == "auxiliary"
    assert token_point.token_type == "input"
    assert token_point.value == 525.0

    cost_point = next(p for p in points if p.metric == "cost")
    assert cost_point.value == 0.0006
    assert cost_point.token_type is None


def test_parse_export_request_ignores_unrelated_metrics():
    raw = _build_export([("claude_code.session.count", 1.0, {"start_type": "fresh"})])
    assert cco.parse_export_request(raw) == []


def test_ingest_and_rollup_sums_delta_points_across_exports():
    # First export interval.
    raw1 = _build_export(
        [
            ("claude_code.token.usage", 100.0, {"model": "claude-sonnet-5", "query_source": "main", "type": "input"}),
            ("claude_code.token.usage", 40.0, {"model": "claude-sonnet-5", "query_source": "main", "type": "output"}),
            ("claude_code.cost.usage", 0.01, {"model": "claude-sonnet-5", "query_source": "main"}),
        ]
    )
    # A later export interval for the same series — since these are DELTA
    # points, this must ADD to the running total, not replace it.
    raw2 = _build_export(
        [
            ("claude_code.token.usage", 50.0, {"model": "claude-sonnet-5", "query_source": "main", "type": "input"}),
        ]
    )
    # A sub-agent on a different model — the whole point of #68.
    raw3 = _build_export(
        [
            ("claude_code.token.usage", 500.0, {"model": "claude-haiku-4-5-20251001", "query_source": "subagent", "type": "input"}),
            ("claude_code.token.usage", 120.0, {"model": "claude-haiku-4-5-20251001", "query_source": "subagent", "type": "output"}),
        ]
    )

    assert cco.ingest_export_request(raw1) == 3
    assert cco.ingest_export_request(raw2) == 1
    assert cco.ingest_export_request(raw3) == 2

    summary = cco.get_usage_summary(period="all")
    rows = {(r["model"], r["query_source"]): r for r in summary["rows"]}

    sonnet = rows[("Sonnet", "main")]
    assert sonnet["input"] == 150  # 100 + 50, summed across export intervals
    assert sonnet["output"] == 40
    assert sonnet["cost_usd"] == 0.01

    haiku = rows[("Haiku", "subagent")]
    assert haiku["input"] == 500
    assert haiku["output"] == 120

    assert summary["totals"]["input"] == 650
    assert summary["source"] == "otel"


def test_persisted_log_never_contains_pii(tmp_path):
    raw = _build_export(
        [
            (
                "claude_code.token.usage",
                10.0,
                {
                    "model": "claude-opus-4-8",
                    "query_source": "main",
                    "type": "input",
                    "user.email": "roberto.ferraro@gmail.com",
                    "user.account_uuid": "aac71c71-9cf4-49d1-a047-4f60195861bd",
                    "organization.id": "0e6251a7-ff93-4f26-9e43-be5035874008",
                    "session.id": "d07a580e-950a-4c55-ae89-9b825e5b5d4d",
                    "terminal.type": "mingw64",
                },
            )
        ]
    )
    cco.ingest_export_request(raw)
    contents = cco._DATA_FILE.read_text(encoding="utf-8")
    assert "roberto.ferraro@gmail.com" not in contents
    assert "aac71c71-9cf4-49d1-a047-4f60195861bd" not in contents
    assert "0e6251a7-ff93-4f26-9e43-be5035874008" not in contents
    assert "d07a580e-950a-4c55-ae89-9b825e5b5d4d" not in contents
    assert "mingw64" not in contents
    assert "claude-opus-4-8" in contents


def test_ingest_malformed_bytes_never_raises():
    assert cco.ingest_export_request(b"not a protobuf export") == 0


def test_get_usage_summary_empty_when_no_file():
    summary = cco.get_usage_summary(period="today")
    assert summary["rows"] == []
    assert summary["totals"]["input"] == 0


def test_get_usage_summary_invalid_period_falls_back_to_all():
    raw = _build_export(
        [("claude_code.token.usage", 5.0, {"model": "claude-opus-4-8", "query_source": "main", "type": "input"})]
    )
    cco.ingest_export_request(raw)
    summary = cco.get_usage_summary(period="bogus")
    assert summary["period"] == "all"
    assert summary["totals"]["input"] == 5
