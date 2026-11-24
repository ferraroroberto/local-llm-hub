"""GET/PUT /admin/api/worker-pools: the Settings › Worker pools sheet's backend."""

from __future__ import annotations

import json
import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src import server as server_mod  # noqa: E402
from src.concurrency import pool_sizes  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pool_sizes, "DEFAULT_PATH", tmp_path / "worker_pools.json")
    return TestClient(server_mod.app)


def test_get_lists_the_six_pools_with_defaults(client):
    body = client.get("/admin/api/worker-pools").json()
    assert [p["id"] for p in body["pools"]] == ["hot-poll", "heavy-read", "action", "fan-out", "background", "media"]
    assert {p["id"]: p["size"] for p in body["pools"]} == pool_sizes.DEFAULTS
    assert all(p["description"] and p["size"] == p["default"] for p in body["pools"])
    assert (body["min"], body["max"]) == (1, 64)
    assert body["restart_pending"] is False


def test_put_saves_and_reports_a_pending_restart(client, tmp_path):
    r = client.put("/admin/api/worker-pools", json={"sizes": {"action": 20}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {p["id"]: p["size"] for p in body["pools"]}["action"] == 20
    assert body["restart_pending"] is True
    assert json.loads((tmp_path / "worker_pools.json").read_text(encoding="utf-8")) == {"action": 20}


@pytest.mark.parametrize("sizes", [{"action": 0}, {"action": 65}, {"action": "many"}, {"action": None}])
def test_put_refuses_a_bad_size_with_the_reason(client, tmp_path, sizes):
    r = client.put("/admin/api/worker-pools", json={"sizes": sizes})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("action: size must be")
    assert not (tmp_path / "worker_pools.json").exists()


def test_put_refuses_a_body_without_sizes(client):
    assert client.put("/admin/api/worker-pools", json={}).status_code == 400
