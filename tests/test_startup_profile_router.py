"""Unit tests for app_web/routers/startup_profile.py (issue #265).

GET returns the current profile + eligible-item metadata; PATCH merges a
partial payload over the current profile, validates, and persists.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "pc-cuda")

from fastapi.testclient import TestClient  # noqa: E402

from src import server as server_mod  # noqa: E402
from src import startup_profile as sp  # noqa: E402


def _isolate_profile(monkeypatch, tmp_path, initial=None):
    target = tmp_path / "startup_profile.json"
    if initial is not None:
        target.write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(sp, "DEFAULT_PROFILE_PATH", target)
    return target


def test_get_returns_profile_and_eligible_items(monkeypatch, tmp_path):
    _isolate_profile(monkeypatch, tmp_path, {
        "docker": True, "langfuse": False, "mac_mini_sync": True,
        "models": ["piper"],
    })
    client = TestClient(server_mod.app)
    r = client.get("/admin/api/startup-profile")
    assert r.status_code == 200
    body = r.json()
    assert body["profile"]["docker"] is True
    assert body["profile"]["langfuse"] is False
    assert body["profile"]["models"] == ["piper"]
    service_ids = {s["id"] for s in body["services"]}
    assert service_ids == {"docker", "langfuse", "mac_mini_sync", "agentsview"}
    assert isinstance(body["models"], list)


def test_patch_merges_partial_payload(monkeypatch, tmp_path):
    target = _isolate_profile(monkeypatch, tmp_path, {
        "docker": True, "langfuse": True, "mac_mini_sync": True,
        "models": ["piper"],
    })
    client = TestClient(server_mod.app)
    r = client.patch("/admin/api/startup-profile", json={"docker": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["profile"]["docker"] is False
    # Untouched fields survive the merge.
    assert body["profile"]["langfuse"] is True
    assert body["profile"]["models"] == ["piper"]

    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk["docker"] is False


def test_patch_validates_model_ids_against_launchable_set(monkeypatch, tmp_path):
    _isolate_profile(monkeypatch, tmp_path, {
        "docker": True, "langfuse": True, "mac_mini_sync": True, "models": [],
    })
    monkeypatch.setattr("src.model_registry.launchable_local_ids", lambda host=None: ["piper"])
    client = TestClient(server_mod.app)
    r = client.patch("/admin/api/startup-profile", json={"models": ["piper", "not-a-real-id"]})
    assert r.status_code == 200, r.text
    assert r.json()["profile"]["models"] == ["piper"]


def test_patch_rejects_bad_shape(monkeypatch, tmp_path):
    _isolate_profile(monkeypatch, tmp_path, {"docker": True, "langfuse": True, "mac_mini_sync": True, "models": []})
    client = TestClient(server_mod.app)
    r = client.patch("/admin/api/startup-profile", json={"models": "not-a-list"})
    assert r.status_code == 400
