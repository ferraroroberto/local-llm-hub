"""ETag + 304 on the /admin entry document (#646, playbook P3).

The stamped HTML names only the assets it references directly, so a changed
transitive module leaves it byte-identical: the validator also keys on the git
sha and the fleet asset hash, or a 304 would pin a phone to an out-of-date build.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app_web import server as admin_server
from app_web.routers import misc
from src.webapp_config import WebappConfig


def _app_client() -> TestClient:
    app = admin_server.create_app()
    app.state.webapp_config = WebappConfig(auth_token="")
    return TestClient(app)


def _etag(client: TestClient) -> str:
    r = client.get("/")
    assert r.status_code == 200
    return r.headers["etag"]


def test_index_sends_a_weak_etag_and_keeps_no_cache():
    r = _app_client().get("/")
    assert r.headers["etag"].startswith('W/"')
    assert r.headers["cache-control"] == "no-cache, must-revalidate"


def test_matching_if_none_match_gets_a_bodyless_304():
    client = _app_client()
    tag = _etag(client)
    r = client.get("/", headers={"If-None-Match": tag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == tag
    assert r.headers["cache-control"] == "no-cache, must-revalidate"


def test_if_none_match_tolerates_strong_form_lists_and_star():
    client = _app_client()
    tag = _etag(client)
    for header in (tag[2:], f'"other", {tag}', "*"):
        assert client.get("/", headers={"If-None-Match": header}).status_code == 304
    assert client.get("/", headers={"If-None-Match": 'W/"stale"'}).status_code == 200


def test_etag_changes_for_a_new_commit(monkeypatch):
    client = _app_client()
    before = _etag(client)
    monkeypatch.setattr(misc, "git_sha", lambda: "deadbee")
    assert _etag(client) != before


def test_etag_changes_when_a_transitive_module_changes():
    # Same stamped HTML, different asset graph: only the fleet hash differs.
    client = _app_client()
    before = _etag(client)
    client.app.state.asset_hashes = {k: "ffffffff" for k in client.app.state.asset_hashes}
    assert _etag(client) != before


def test_etag_changes_when_index_html_is_edited(tmp_path, monkeypatch):
    client = _app_client()
    before = _etag(client)
    (tmp_path / "index.html").write_text(
        (misc.STATIC_DIR / "index.html").read_text(encoding="utf-8") + "<!-- edit -->",
        encoding="utf-8",
    )
    monkeypatch.setattr(misc, "STATIC_DIR", tmp_path)
    assert _etag(client) != before
