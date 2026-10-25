"""Unit tests for app_web/routers/glossary.py (issue #94).

GET/PUT round-trip against a temp glossary file, validation → 400, cache
invalidation on save, and the /mine endpoint with the miner mocked.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

from fastapi.testclient import TestClient  # noqa: E402

from src import server as server_mod  # noqa: E402
from src import transcription_glossary as tg  # noqa: E402


def _isolate_glossary(monkeypatch, tmp_path):
    """Point the glossary loaders/savers at a temp file, clearing caches."""
    target = tmp_path / "glossary.json"
    target.write_text(
        json.dumps({"replacements": [{"from": "quen", "to": "Qwen"}],
                    "boost_terms": ["Qwen"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(tg, "DEFAULT_GLOSSARY_PATH", target)
    tg.load_rules.cache_clear()
    return target


def test_get_glossary_returns_editable_shape(monkeypatch, tmp_path):
    _isolate_glossary(monkeypatch, tmp_path)
    client = TestClient(server_mod.app)
    r = client.get("/admin/api/glossary")
    assert r.status_code == 200
    body = r.json()
    assert body["replacements"] == [{"from": "quen", "to": "Qwen"}]
    assert body["boost_terms"] == ["Qwen"]


def test_put_persists_and_normalizes(monkeypatch, tmp_path):
    target = _isolate_glossary(monkeypatch, tmp_path)
    client = TestClient(server_mod.app)
    r = client.put("/admin/api/glossary", json={
        "replacements": [
            {"from": "  cloud code  ", "to": "Claude Code"},
            {"from": "", "to": "dropped"},  # blank source → dropped
        ],
        "boost_terms": ["Codex", "codex", " Qwen "],  # case-dupe dropped + trimmed
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["boost_terms_need_restart"] is True
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["replacements"] == [{"from": "cloud code", "to": "Claude Code"}]
    assert saved["boost_terms"] == ["Codex", "Qwen"]


def test_put_invalidates_replacement_cache(monkeypatch, tmp_path):
    _isolate_glossary(monkeypatch, tmp_path)
    # Prime the cache with the seeded rule.
    assert any(r.replacement == "Qwen" for r in tg.load_rules())
    client = TestClient(server_mod.app)
    client.put("/admin/api/glossary", json={
        "replacements": [{"from": "cloud code", "to": "Claude Code"}],
        "boost_terms": [],
    })
    # After save the cache must reflect the new rule, not the stale one.
    repls = {r.replacement for r in tg.load_rules()}
    assert "Claude Code" in repls
    assert "Qwen" not in repls


def test_put_rejects_bad_shape(monkeypatch, tmp_path):
    _isolate_glossary(monkeypatch, tmp_path)
    client = TestClient(server_mod.app)
    r = client.put("/admin/api/glossary", json={"replacements": "nope", "boost_terms": []})
    assert r.status_code == 400


def test_mine_endpoint_returns_suggestions(monkeypatch, tmp_path):
    _isolate_glossary(monkeypatch, tmp_path)
    from src import dictionary_miner as dm

    async def _fake_mine(days=None):
        return {
            "boost_terms": [{"term": "Claude Code", "count": 3}],
            "replacements": [{"from": "quen", "to": "Qwen"}],
            "meta": {"days": days or 7, "n_sessions": 2, "vt_git_sha": "x", "llm_used": False},
        }

    monkeypatch.setattr(dm, "mine_suggestions", _fake_mine)
    client = TestClient(server_mod.app)
    r = client.post("/admin/api/glossary/mine", json={"days": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["days"] == 5
    assert body["boost_terms"][0]["term"] == "Claude Code"


def test_mine_endpoint_maps_miner_error_to_502(monkeypatch, tmp_path):
    _isolate_glossary(monkeypatch, tmp_path)
    from src import dictionary_miner as dm

    async def _boom(days=None):
        raise dm.MinerError("voice-transcriber unreachable")

    monkeypatch.setattr(dm, "mine_suggestions", _boom)
    client = TestClient(server_mod.app)
    r = client.post("/admin/api/glossary/mine", json={})
    assert r.status_code == 502
    assert "unreachable" in r.json()["detail"]
