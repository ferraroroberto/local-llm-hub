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
