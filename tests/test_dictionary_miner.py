"""Unit tests for src/dictionary_miner.py (issue #94).

Covers the pure heuristic/clustering helpers and the orchestrator with the
corpus fetch + LLM pass mocked out — no network, no live voice-transcriber.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest  # noqa: E402

from src import dictionary_miner as dm  # noqa: E402


def _run(coro):
    """Drive a coroutine on a fresh loop/thread (matches the suite pattern)."""
    import threading

    bucket: dict = {}

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            bucket["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001
            bucket["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if "error" in bucket:
        raise bucket["error"]
    return bucket["value"]


# --------------------------------------------------------------- heuristics

_CORPUS = [
    "We shipped Claude Code.",
    "I tested Claude Code again with Codex and Qwen.",
    "Then Claude Code worked.",
]


def test_heuristic_surfaces_recurring_phrase():
    out = dm.heuristic_candidates(_CORPUS, existing_boost=[], min_count=2)
    terms = [c["term"] for c in out]
    assert "Claude Code" in terms
    # All returned candidates respect the min_count floor.
    assert all(c["count"] >= 2 for c in out)


def test_heuristic_excludes_already_boosted():
    out = dm.heuristic_candidates(_CORPUS, existing_boost=["Claude Code"], min_count=2)
    terms = [c["term"] for c in out]
    assert "Claude Code" not in terms


def test_heuristic_ignores_sentence_initial_and_stopwords():
    # "Then" is capitalised only because it starts a sentence — never a term.
    out = dm.heuristic_candidates(_CORPUS, existing_boost=[], min_count=1)
    terms = {c["term"] for c in out}
    assert "Then" not in terms
    assert "We" not in terms


def test_heuristic_ranked_by_frequency():
    out = dm.heuristic_candidates(_CORPUS, existing_boost=[], min_count=2)
    counts = [c["count"] for c in out]
    assert counts == sorted(counts, reverse=True)


# --------------------------------------------------------- cluster → rules

def test_clusters_to_replacements_keeps_only_corpus_variants():
    clusters = [
        {"canonical": "Qwen", "variants": ["quen", "Qwen", "kwen"]},
    ]
    transcripts = ["I ran quen locally"]  # only "quen" appears
    out = dm._clusters_to_replacements(clusters, transcripts)
    assert out == [{"from": "quen", "to": "Qwen"}]


def test_clusters_to_replacements_dedupes_and_skips_canonical():
    clusters = [
        {"canonical": "Codex", "variants": ["code x", "code x", "Codex"]},
    ]
    transcripts = ["use code x please"]
    out = dm._clusters_to_replacements(clusters, transcripts)
    assert out == [{"from": "code x", "to": "Codex"}]


def test_parse_clusters_tolerates_prose_wrapping():
    text = 'Sure! Here you go:\n[{"canonical":"Qwen","variants":["quen"]}]\nDone.'
    assert dm._parse_clusters(text) == [{"canonical": "Qwen", "variants": ["quen"]}]


def test_parse_clusters_bad_json_returns_empty():
    assert dm._parse_clusters("not json at all") == []


# --------------------------------------------------------------- fetch

class _FakeResp:
    def __init__(self, payload, success=True, status=200):
        self._payload = payload
        self.is_success = success
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.is_success:
            import httpx

            raise httpx.HTTPStatusError("boom", request=None, response=None)


def _fake_client(routes):
    """Build a fake httpx.AsyncClient class returning canned routes by path."""

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            for needle, resp in routes.items():
                if needle in url:
                    if isinstance(resp, Exception):
                        raise resp
                    return resp
            raise AssertionError(f"unexpected GET {url}")

    return _Client


def test_fetch_transcripts_budget_truncates(monkeypatch):
    body = {"transcripts": [
        {"transcript": "alpha beta gamma"},
        {"transcript": "delta epsilon zeta"},
    ]}
    routes = {
        "/api/sessions/transcripts": _FakeResp(body),
        "/api/version": _FakeResp({"git_sha": "abc123"}),
    }
    monkeypatch.setattr(dm.httpx, "AsyncClient", _fake_client(routes))
    texts, sha = _run(dm.fetch_transcripts("https://x", days=7, max_tokens=3))
    # Budget of 3 words stops after the first 3-word transcript.
    assert texts == ["alpha beta gamma"]
    assert sha == "abc123"


def test_fetch_transcripts_raises_on_transport_error(monkeypatch):
    import httpx

    routes = {"/api/sessions/transcripts": httpx.ConnectError("refused")}
    monkeypatch.setattr(dm.httpx, "AsyncClient", _fake_client(routes))
    with pytest.raises(dm.MinerError):
        _run(dm.fetch_transcripts("https://x", days=7, max_tokens=100))


# --------------------------------------------------------- orchestration

def test_mine_suggestions_heuristics_only(monkeypatch):
    monkeypatch.setattr(dm, "load_miner_config", lambda path=None: {
        "voice_transcriber_base_url": "https://x",
        "default_days": 5, "max_tokens": 1000, "min_count": 2,
        "use_llm": False, "llm_model": "claude-haiku-4-5",
    })

    async def _fake_fetch(base, days, budget):
        return list(_CORPUS), "sha9"

    monkeypatch.setattr(dm, "fetch_transcripts", _fake_fetch)
    monkeypatch.setattr(dm, "load_glossary", lambda path=None: {"replacements": [], "boost_terms": []})

    out = _run(dm.mine_suggestions())
    assert out["meta"]["days"] == 5
    assert out["meta"]["n_sessions"] == 3
    assert out["meta"]["vt_git_sha"] == "sha9"
    assert out["meta"]["llm_used"] is False
    assert out["replacements"] == []
    assert any(c["term"] == "Claude Code" for c in out["boost_terms"])


def test_mine_suggestions_llm_failure_falls_back(monkeypatch):
    monkeypatch.setattr(dm, "load_miner_config", lambda path=None: {
        "voice_transcriber_base_url": "https://x",
        "default_days": 7, "max_tokens": 1000, "min_count": 2,
        "use_llm": True, "llm_model": "claude-haiku-4-5",
    })

    async def _fake_fetch(base, days, budget):
        return list(_CORPUS), None

    async def _boom_llm(*a, **kw):
        return []  # simulates graceful degradation inside the LLM pass

    monkeypatch.setattr(dm, "fetch_transcripts", _fake_fetch)
    monkeypatch.setattr(dm, "load_glossary", lambda path=None: {"replacements": [], "boost_terms": []})
    monkeypatch.setattr(dm, "llm_cluster_replacements", _boom_llm)

    out = _run(dm.mine_suggestions(days=3))
    assert out["meta"]["llm_used"] is True
    assert out["replacements"] == []
    assert out["boost_terms"]  # heuristics still produced suggestions
