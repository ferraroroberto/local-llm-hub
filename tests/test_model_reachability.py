"""The reachability probe shared by the Models and Playground tabs (#708)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src import model_reachability as mr


def _m(mid, backend="tts", port=9000):
    return SimpleNamespace(id=mid, backend=backend, port=port)


def test_local_reachability_gates_on_bound_ports(monkeypatch):
    probed = []

    def fake_is_reachable(model, timeout):
        probed.append(model.id)
        return model.id == "up"

    monkeypatch.setattr(mr.bp, "is_reachable", fake_is_reachable)
    models = [
        _m("up", port=1), _m("down", port=2), _m("unbound", port=3),
        _m("noport", port=None), _m("sub", backend="claude", port=None),
    ]
    got = asyncio.run(mr.local_reachability(models, {1: [10], 2: [11]}))
    assert got == [True, False, False, False, True]
    assert sorted(probed) == ["down", "up"]  # unbound / no-port rows never probed


def test_reachability_by_id_splits_local_and_peer_owned(monkeypatch):
    monkeypatch.setattr(mr, "resolve_host", lambda: SimpleNamespace(id="here"))
    owners = {"mine": None, "theirs": "peer", "gone": "peer2"}
    monkeypatch.setattr(mr, "effective_owner", lambda m: owners[m.id])
    monkeypatch.setattr(mr, "snapshot_listening_pids", lambda: {1: [1]})
    monkeypatch.setattr(mr.bp, "is_reachable", lambda m, t: True)
    monkeypatch.setattr(
        mr, "get_host", lambda hid: SimpleNamespace(id=hid) if hid == "peer" else None
    )

    async def fake_remote_models(host):
        return [{"id": "theirs", "reachable": True}]

    monkeypatch.setattr(mr.remote_stats, "remote_models", fake_remote_models)
    got = asyncio.run(mr.reachability_by_id(
        [_m("mine", port=1), _m("theirs"), _m("gone")]
    ))
    assert got == {"mine": True, "theirs": True, "gone": False}
