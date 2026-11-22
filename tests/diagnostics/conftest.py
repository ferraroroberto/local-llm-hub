"""Keep the real storage walks out of every diagnostics test.

A capture's storage section sizes folders and calls the docker CLI on the host
running the tests, which takes seconds and reads the developer's disk. Tests
that exercise it import ``storage.collect`` by reference at module load (before
this fixture runs) and pass an explicit config."""

from __future__ import annotations

import pytest

from src.diagnostics import storage


@pytest.fixture(autouse=True)
def _stub_storage_collect(monkeypatch):
    monkeypatch.setattr(
        storage, "collect",
        lambda cfg=None, **kw: {"schema": storage.STORAGE_SCHEMA, "collected_at": 0.0,
                                "elapsed_s": 0.0,
                                "drives": {"status": "ok", "items": []},
                                "docker": storage.unknown("stubbed (test)"),
                                "growers": {"status": "ok", "items": []},
                                "top_folders": {"status": "ok", "roots": []}},
    )
