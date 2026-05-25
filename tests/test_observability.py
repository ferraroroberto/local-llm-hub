"""Unit tests for src/observability.py helpers.

These run with OTEL_SDK_DISABLED=true (set in tests/conftest.py) so
init_otel() is a no-op and the helper functions take the disabled-mode
path. That's the right contract to test — if the helpers misbehave when
OTel is off, every other test in the suite would be hostage to a live
OTLP endpoint.
"""

from __future__ import annotations

import os
import uuid

import pytest

from src import observability as obs


def test_is_sdk_disabled_reads_env():
    assert obs.is_sdk_disabled() is True


def test_hash_prompts_default_off(monkeypatch):
    monkeypatch.delenv("OTEL_HASH_PROMPTS", raising=False)
    assert obs.hash_prompts_enabled() is False
    monkeypatch.setenv("OTEL_HASH_PROMPTS", "true")
    assert obs.hash_prompts_enabled() is True
    monkeypatch.setenv("OTEL_HASH_PROMPTS", "false")
    assert obs.hash_prompts_enabled() is False
    monkeypatch.setenv("OTEL_HASH_PROMPTS", "1")
    assert obs.hash_prompts_enabled() is True


def test_init_otel_disabled_is_idempotent():
    obs._reset_for_tests()
    assert obs.init_otel("test-service") is False
    assert obs.init_otel("test-service") is False
    assert obs.genai_meters() is None


def test_backend_to_genai_system_mapping():
    assert obs.backend_to_genai_system("claude") == "anthropic"
    assert obs.backend_to_genai_system("gemini") == "google_genai"
    assert obs.backend_to_genai_system("openai") == "llama_cpp"
    assert obs.backend_to_genai_system("whisper") == "whisper"
    # Unknown backends pass through as the raw value so the chart still
    # has a non-empty bucket label.
    assert obs.backend_to_genai_system("mystery") == "mystery"
    assert obs.backend_to_genai_system("") == "unknown"


def test_derive_trace_id_from_uuid_is_deterministic():
    u = "9e108e0e-3a5b-4d8c-9f10-1234567890ab"
    a = obs.derive_trace_id_from_uuid(u)
    b = obs.derive_trace_id_from_uuid(u)
    assert a is not None
    assert a == b
    # 128-bit value
    assert 0 < a < (1 << 128)


def test_derive_trace_id_treats_32_hex_as_existing_otel_id():
    """A 32-char hex string is treated as "this is already an OTel
    trace ID, use it verbatim". A hyphenated UUID gets BLAKE2b-normalised
    instead — by design — because callers expect "give me a fresh trace
    ID, here's my UUID" to land on a stable derived value rather than
    coincidentally collide with a pre-existing trace.

    Pins the documented behaviour from observability.derive_trace_id_from_uuid.
    """
    u = uuid.uuid4()
    hyphenated = obs.derive_trace_id_from_uuid(str(u))
    compact_passthrough = obs.derive_trace_id_from_uuid(u.hex)
    # 32-hex passthrough returns the raw integer.
    assert compact_passthrough == int(u.hex, 16)
    # Hyphenated form goes through BLAKE2b — different value.
    assert hyphenated != compact_passthrough
    # But hyphenated is itself deterministic.
    again = obs.derive_trace_id_from_uuid(str(u))
    assert hyphenated == again


def test_derive_trace_id_from_32_hex_passes_through():
    raw = "0123456789abcdef0123456789abcdef"
    derived = obs.derive_trace_id_from_uuid(raw)
    assert derived == int(raw, 16)


def test_derive_trace_id_from_empty_returns_none():
    assert obs.derive_trace_id_from_uuid("") is None
    assert obs.derive_trace_id_from_uuid("   ") is None


def test_derive_trace_id_from_garbage_still_deterministic():
    # Arbitrary opaque strings are BLAKE2b'd so callers using e.g.
    # "session-42" still get correlation.
    a = obs.derive_trace_id_from_uuid("session-42")
    b = obs.derive_trace_id_from_uuid("session-42")
    c = obs.derive_trace_id_from_uuid("session-43")
    assert a is not None and b is not None and c is not None
    assert a == b
    assert a != c


def test_set_genai_payload_no_op_on_none_span():
    # Must not raise when the span is None (disabled-mode path).
    obs.set_genai_payload(None, "prompt text", "completion text")


def test_set_genai_request_attrs_no_op_on_none_span():
    obs.set_genai_request_attrs(
        None, model="claude-haiku", backend="claude",
        temperature=0.7, max_tokens=128, client_id="test",
    )


def test_record_genai_metrics_no_op_without_meters():
    # No meters when OTel is disabled. Must not raise.
    assert obs.genai_meters() is None
    obs.record_genai_metrics(
        model="x", backend="claude", route="/v1/messages",
        client_id="t", duration_ms=42.0, input_tokens=10, output_tokens=20,
    )


class _FakeSpan:
    def __init__(self):
        self.attrs: dict = {}
        self.events: list = []

    def set_attribute(self, k, v):
        self.attrs[k] = v

    def add_event(self, name, attributes=None):
        self.events.append((name, dict(attributes or {})))


def test_set_genai_payload_raw_mode_truncates(monkeypatch):
    monkeypatch.delenv("OTEL_HASH_PROMPTS", raising=False)
    span = _FakeSpan()
    big = "A" * (obs._MAX_INLINE_PAYLOAD + 100)
    obs.set_genai_payload(span, big, "short reply")
    assert span.attrs["gen_ai.prompt"].startswith("AAAA")
    assert len(span.attrs["gen_ai.prompt"]) == obs._MAX_INLINE_PAYLOAD
    assert span.attrs["gen_ai.prompt.truncated"] is True
    assert span.attrs["gen_ai.prompt.original_length"] == len(big)
    assert span.attrs["gen_ai.completion"] == "short reply"


def test_set_genai_payload_hash_mode(monkeypatch):
    monkeypatch.setenv("OTEL_HASH_PROMPTS", "true")
    span = _FakeSpan()
    obs.set_genai_payload(span, "hello world", "hi back")
    assert span.attrs["gen_ai.prompt"].startswith("blake2b:")
    assert span.attrs["gen_ai.completion"].startswith("blake2b:")


def test_set_genai_request_attrs_writes_all_fields():
    span = _FakeSpan()
    obs.set_genai_request_attrs(
        span, model="qwen3.5-4b", backend="openai",
        temperature=0.5, max_tokens=256, client_id="vt",
    )
    assert span.attrs["gen_ai.system"] == "llama_cpp"
    assert span.attrs["gen_ai.operation.name"] == "chat"
    assert span.attrs["gen_ai.request.model"] == "qwen3.5-4b"
    assert span.attrs["gen_ai.request.temperature"] == 0.5
    assert span.attrs["gen_ai.request.max_tokens"] == 256
    assert span.attrs["client.id"] == "vt"


def test_set_genai_response_attrs_writes_what_is_given():
    span = _FakeSpan()
    obs.set_genai_response_attrs(
        span, input_tokens=10, output_tokens=20,
        finish_reason="end_turn", response_id="msg_xyz",
    )
    assert span.attrs["gen_ai.usage.input_tokens"] == 10
    assert span.attrs["gen_ai.usage.output_tokens"] == 20
    assert span.attrs["gen_ai.response.finish_reasons"] == ["end_turn"]
    assert span.attrs["gen_ai.response.id"] == "msg_xyz"
