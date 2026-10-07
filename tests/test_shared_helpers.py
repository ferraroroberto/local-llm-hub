"""Unit tests for the helpers consolidated out of copy-pasted call sites (#705)."""

from __future__ import annotations

from pathlib import Path

from src import server_common, usage_pricing
from src.atomic_write import atomic_write_text
from src.usage_common import iter_jsonl


def test_atomic_write_text_creates_parents_and_leaves_no_tmp(tmp_path: Path):
    target = tmp_path / "nested" / "dir" / "state.json"
    atomic_write_text(target, '{"a": 1}\n')
    assert target.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert not (target.parent / "state.json.tmp").exists()
    atomic_write_text(target, "second\n")
    assert target.read_text(encoding="utf-8") == "second\n"


def test_iter_jsonl_skips_blank_and_corrupt_lines(tmp_path: Path):
    f = tmp_path / "x.jsonl"
    f.write_bytes(b'{"a": 1}\n\n  \nnot json\n{"b": 2}\n\xff\xfe{"c": 3}\n')
    assert list(iter_jsonl(f)) == [{"a": 1}, {"b": 2}]


def test_iter_jsonl_missing_file_raises_oserror(tmp_path: Path):
    try:
        list(iter_jsonl(tmp_path / "nope.jsonl"))
    except OSError:
        return
    raise AssertionError("expected OSError")


class _Span:
    def __init__(self):
        self.attrs = {}

    def set_attribute(self, key, value):
        self.attrs[key] = value


def test_set_span_attrs_keeps_falsy_values_and_drops_none():
    span = _Span()
    server_common.set_span_attrs(
        span, "t", {"a": 0, "b": False, "c": None, "d": "x"}
    )
    assert span.attrs == {"a": 0, "b": False, "d": "x"}


def test_set_span_attrs_noop_without_span_and_swallows_errors():
    server_common.set_span_attrs(None, "t", {"a": 1})
    server_common.set_span_attrs(object(), "t", {"a": 1})

    class Boom(_Span):
        def set_attribute(self, key, value):
            raise RuntimeError("telemetry down")

    server_common.set_span_attrs(Boom(), "t", {"a": 1})


def test_record_route_metrics_derives_duration(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        server_common, "record_genai_metrics", lambda **kw: seen.update(kw)
    )
    monkeypatch.setattr(server_common.time, "monotonic_ns", lambda: 5_000_000)
    server_common.record_route_metrics(
        2_000_000, model="m", backend="b", route="/r", client_id="c",
        error_type="http_500",
    )
    assert seen == {
        "model": "m", "backend": "b", "route": "/r", "client_id": "c",
        "duration_ms": 3.0, "error_type": "http_500",
    }


def test_missing_pricing_file_prices_at_zero(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(usage_pricing, "_priced_table_cache", {})
    monkeypatch.setattr(usage_pricing, "_PRICING_PATH", tmp_path / "absent.json")
    assert usage_pricing.load_pricing() == {}


def test_committed_pricing_files_load():
    cache = usage_pricing._priced_table_cache
    cache.clear()
    try:
        assert "Opus" in usage_pricing.load_pricing()
        assert "GPT-5.5" in usage_pricing.load_openai_pricing()
        assert "pro" in usage_pricing.load_gemini_pricing()
    finally:
        cache.clear()
