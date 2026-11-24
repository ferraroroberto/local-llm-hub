"""The build readout's restart route is mounted on the /admin sub-app behind the bearer gate.

``src/self_restart.py`` is vendored verbatim and tested in project-scaffolding; this covers the
app-side wiring. No test posts a same-origin restart through the real app, because that would run
the real ``tray.bat --restart``.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app_web import server as admin_server
from app_web.middleware import require_authenticated
from src.self_restart import TRAY_RESTART, RestartTarget, restart_router
from src.webapp_config import WebappConfig

_PROXY_HEADERS = {"X-Forwarded-For": "203.0.113.5"}  # forces is_loopback=False
TOKEN = "secret123"
ORIGIN = {"Origin": "http://testserver"}
ROOT = Path(__file__).resolve().parents[2]


def _admin_client(token: str = TOKEN) -> TestClient:
    app = admin_server.create_app()
    app.state.webapp_config = WebappConfig(auth_token=token)
    return TestClient(app)


def test_the_app_declares_the_tray_recipe_as_its_only_target():
    res = _admin_client().get("/api/restart")  # loopback: no token needed
    assert res.status_code == 200
    assert [t["id"] for t in res.json()["targets"]] == ["app"]


def test_a_remote_caller_without_the_token_cannot_read_or_start_a_restart():
    client = _admin_client()
    assert client.get("/api/restart", headers=_PROXY_HEADERS).status_code == 401
    res = client.post("/api/restart", json={"target": "app"}, headers={**_PROXY_HEADERS, **ORIGIN})
    assert res.status_code == 401


def test_a_cross_origin_post_is_refused_even_with_the_token():
    res = _admin_client().post(
        "/api/restart", json={"target": "app"},
        headers={**_PROXY_HEADERS, "Authorization": f"Bearer {TOKEN}", "Origin": "https://elsewhere.example"},
    )
    assert res.status_code == 403


def test_the_route_refuses_when_the_middleware_is_missing(tmp_path: Path):
    bare = FastAPI()
    bare.include_router(restart_router(
        targets=[RestartTarget("app", "Restart app", TRAY_RESTART)],
        status_dir=tmp_path / "restart", guard=require_authenticated, root=tmp_path,
        spawn=lambda spec, root: pytest.fail("a restart started without auth"),
    ))
    res = TestClient(bare).post("/api/restart", json={"target": "app"}, headers=ORIGIN)
    assert res.status_code == 401


def test_the_recipe_runs_the_repos_own_tray_bat():
    assert TRAY_RESTART[-2:] == (".\\tray.bat", "--restart")
    assert (ROOT / "tray.bat").is_file()


def test_the_sheet_is_wired_to_the_update_routes_the_admin_app_serves():
    # The vendored sheet defaults to /api/restart/{check,pull}; the SPA must name the /admin
    # spellings, and each must resolve on the sub-app (a missing route is 404, a guarded one 401).
    main_js = (admin_server.STATIC_DIR / "main.js").read_text(encoding="utf-8")
    client = _admin_client()
    for option, path in (("checkEndpoint", "/api/restart/check"), ("pullEndpoint", "/api/restart/pull")):
        assert f"{option}: '/admin{path}'" in main_js
        res = client.post(path, headers={**_PROXY_HEADERS, **ORIGIN})
        assert res.status_code == 401, f"{path} is not mounted behind the bearer gate"
