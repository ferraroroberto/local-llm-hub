"""The vendored usage log, wired into the admin sub-app (#777).

The router and its privacy contract are tested upstream in project-scaffolding;
these pin what is specific to this hub: the route sits behind the bearer gate
(a phone presents the token, loopback does not), events land in the runtime
SQLite file, and the client's endpoint and the ``data-track`` ids on the page
agree with the server.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

os.environ.setdefault("LOCAL_LLM_HUB_HOST", "tower")

import pytest
from fastapi.testclient import TestClient

from app_web import server as admin_server
from app_web.routers._helpers import STATIC_DIR
from src.usage_log import ID_PATTERN
from src.webapp_config import WebappConfig

_PROXY_HEADERS = {"X-Forwarded-For": "203.0.113.5"}  # forces is_loopback=False
_BATCH = {
    "session_id": "6f1c1c1e-2b7e-4a64-9f3a-0c5d8d0b9a11",
    "device_class": "phone",
    "os_class": "ios",
    "events": [
        {"kind": "session_start"},
        {"kind": "view", "id": "hub"},
        {"kind": "tap", "id": "settings-open", "view": "hub"},
    ],
}


@pytest.fixture
def usage_db(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "usage.sqlite3"
    monkeypatch.setenv("LLM_HUB_USAGE_DB_PATH", str(path))
    return path


def _client(token: str = "") -> TestClient:
    app = admin_server.create_app()
    app.state.webapp_config = WebappConfig(auth_token=token)
    return TestClient(app)


def _rows(path: Path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT kind, view_id, control_id, device_class, os_class FROM events ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_events_from_a_phone_need_the_bearer_token(usage_db):
    client = _client("secret123")
    refused = client.post("/api/usage/events", json=_BATCH, headers=_PROXY_HEADERS)
    assert refused.status_code == 401
    assert not usage_db.exists()

    accepted = client.post(
        "/api/usage/events",
        json=_BATCH,
        headers={**_PROXY_HEADERS, "Authorization": "Bearer secret123"},
    )
    assert accepted.status_code < 300
    assert _rows(usage_db) == [
        ("session_start", None, None, "phone", "ios"),
        ("view", "hub", None, "phone", "ios"),
        ("tap", "hub", "settings-open", "phone", "ios"),
    ]


def test_events_from_loopback_land_without_a_token(usage_db):
    assert _client("secret123").post("/api/usage/events", json=_BATCH).status_code < 300
    assert len(_rows(usage_db)) == 3


def test_stored_columns_hold_no_content(usage_db):
    _client().post("/api/usage/events", json=_BATCH)
    conn = sqlite3.connect(usage_db)
    try:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(events)")]
    finally:
        conn.close()
    assert columns == [
        "id", "ts", "session_id", "kind", "view_id", "control_id", "device_class", "os_class",
    ]


def test_client_endpoint_is_the_mounted_route(usage_db):
    source = (STATIC_DIR / "usage.js").read_text(encoding="utf-8")
    endpoint = re.search(r"USAGE_ENDPOINT = '([^']+)'", source).group(1)
    assert endpoint == "/admin/api/usage/events"
    # Same-origin path only: the vendored client refuses anything else.
    assert endpoint.startswith("/") and not endpoint.startswith("//")
    # Resolve by request, not by scanning ``app.routes``: recent FastAPI keeps an
    # included router as one lazy ``_IncludedRouter`` entry with no ``path``, so
    # a path scan finds nothing on a fresh install (#782). An empty batch hits
    # the handler (200) and records nothing; an unmounted route is a 404.
    sub_path = endpoint.removeprefix("/admin")
    reply = _client().post(sub_path, json={**_BATCH, "events": []})
    assert reply.status_code == 200
    assert not usage_db.exists()


def test_data_track_ids_are_bounded_constants():
    html = "".join(
        (STATIC_DIR / name).read_text(encoding="utf-8") for name in ("index.html", "sheets.html")
    )
    ids = re.findall(r'data-track="([^"]*)"', html)
    assert ids, "no control carries a data-track id"
    bad = [i for i in ids if not ID_PATTERN.fullmatch(i)]
    assert not bad, f"data-track ids the server would drop: {bad}"
