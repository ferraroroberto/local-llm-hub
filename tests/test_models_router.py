"""GET /api/models — device surfaced for reachable TTS backends (#371).

`piper.py` hardcodes CPU regardless of config's `--device` arg (now removed
— see config/models.yaml + test_tts_server.py's hardcode test), so the only
thing left to verify here is the admin payload: a reachable TTS row reads
its resolved device off its own `/health` (tts_server.py's `state.device`)
via `backend_process.probe_health`, a still-loading/unresolved value (e.g.
the raw "auto" arg) or an unreachable row omits the key rather than guessing.
"""

from __future__ import annotations

import os

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

from fastapi.testclient import TestClient  # noqa: E402

from app_web.routers import models as models_router  # noqa: E402
from src import backend_process as bp  # noqa: E402


def _admin_client() -> TestClient:
    from app_web.server import create_app

    return TestClient(create_app())


def _only_piper_listening(monkeypatch, extra_ports: dict | None = None) -> None:
    """Make just piper's port (8096) look bound, so `_probe_reach` only
    fires its HTTP probe at piper — every other local row is skipped by
    the port-not-listening gate and stays unreachable/deviceless."""
    listening = {8096: [4242]}
    if extra_ports:
        listening.update(extra_ports)
    monkeypatch.setattr(models_router, "snapshot_listening_pids", lambda: listening)


def _row(body: dict, model_id: str) -> dict:
    rows = [m for m in body["models"] if m["id"] == model_id]
    assert rows, f"no row for {model_id!r} in {[m['id'] for m in body['models']]}"
    return rows[0]


def test_reachable_tts_row_reports_resolved_device(monkeypatch):
    _only_piper_listening(monkeypatch)
    monkeypatch.setattr(bp, "is_reachable", lambda m, timeout=1.5: True)
    monkeypatch.setattr(bp, "probe_health", lambda m, timeout=0.4: {"device": "cpu", "ready": True})

    resp = _admin_client().get("/api/models", params={"local_only": "true"})
    assert resp.status_code == 200
    row = _row(resp.json(), "piper")
    assert row["reachable"] is True
    assert row["device"] == "cpu"


def test_unreachable_tts_row_omits_device(monkeypatch):
    # No port reported as listening — every row (including piper) stays
    # unreachable, so the device probe never even fires.
    monkeypatch.setattr(models_router, "snapshot_listening_pids", lambda: {})
    monkeypatch.setattr(bp, "probe_health", lambda m, timeout=0.4: (_ for _ in ()).throw(
        AssertionError("probe_health must not be called for an unreachable row")
    ))

    resp = _admin_client().get("/api/models", params={"local_only": "true"})
    row = _row(resp.json(), "piper")
    assert row["reachable"] is False
    assert "device" not in row


def test_loading_tts_row_omits_unresolved_device(monkeypatch):
    """A backend still loading reports its raw (unresolved) `--device` arg
    on /health, e.g. "auto" — that's not a real device, so it must be
    omitted rather than surfaced as if it were the final answer."""
    _only_piper_listening(monkeypatch)
    monkeypatch.setattr(bp, "is_reachable", lambda m, timeout=1.5: True)
    monkeypatch.setattr(bp, "probe_health", lambda m, timeout=0.4: {"device": "auto", "ready": False})

    resp = _admin_client().get("/api/models", params={"local_only": "true"})
    row = _row(resp.json(), "piper")
    assert row["reachable"] is True
    assert "device" not in row


def test_device_probe_not_fired_for_non_tts_backend(monkeypatch):
    """A reachable non-TTS row (e.g. an openai-shaped chat backend) has no
    comparable device concept — the probe must not even fire for it."""
    monkeypatch.setattr(models_router, "snapshot_listening_pids", lambda: {8088: [111]})
    monkeypatch.setattr(bp, "is_reachable", lambda m, timeout=1.5: True)
    monkeypatch.setattr(bp, "probe_health", lambda m, timeout=0.4: (_ for _ in ()).throw(
        AssertionError("probe_health must not be called for a non-TTS backend")
    ))

    resp = _admin_client().get("/api/models", params={"local_only": "true"})
    row = _row(resp.json(), "qwen35_4b")
    assert row["reachable"] is True
    assert "device" not in row


def test_piper_config_carries_no_device_arg():
    """config/models.yaml's piper row must not resurrect the dead
    `--device` arg piper.py never honored (#371) — piper.py's constructor
    hardcodes CPU regardless, so a config arg here would be misleading."""
    from src.model_registry import resolve

    piper = resolve("piper")
    assert piper is not None
    assert "--device" not in (piper.args or [])
