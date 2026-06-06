"""Admin frontier-report route tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app_web import server as admin_server
from app_web.routers import misc
from src.webapp_config import WebappConfig


def _client_with_frontier(tmp_path, monkeypatch, token: str = "") -> TestClient:
    runs_dir = tmp_path / "runs"
    latest_file = runs_dir / "LATEST"
    monkeypatch.setattr(misc, "FRONTIER_RUNS_DIR", runs_dir)
    monkeypatch.setattr(misc, "FRONTIER_LATEST_FILE", latest_file)

    app = admin_server.create_app()
    app.state.webapp_config = WebappConfig(auth_token=token)
    return TestClient(app)


def test_frontier_report_serves_latest_run(tmp_path, monkeypatch):
    client = _client_with_frontier(tmp_path, monkeypatch)
    run_dir = tmp_path / "runs" / "2026-05-10"
    run_dir.mkdir(parents=True)
    (tmp_path / "runs" / "LATEST").write_text("2026-05-10\n", encoding="utf-8")
    (run_dir / "frontier.html").write_text("<html>frontier ok</html>", encoding="utf-8")

    response = client.get("/frontier")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frontier ok" in response.text


def test_frontier_report_missing_latest_is_clear_404(tmp_path, monkeypatch):
    client = _client_with_frontier(tmp_path, monkeypatch)

    response = client.get("/frontier")

    assert response.status_code == 404
    assert response.json()["detail"] == "No frontier report has been generated yet"


def test_frontier_report_keeps_proxy_auth_enforcement(tmp_path, monkeypatch):
    client = _client_with_frontier(tmp_path, monkeypatch, token="secret-token")
    run_dir = tmp_path / "runs" / "2026-05-10"
    run_dir.mkdir(parents=True)
    (tmp_path / "runs" / "LATEST").write_text("2026-05-10\n", encoding="utf-8")
    (run_dir / "frontier.html").write_text("<html>frontier ok</html>", encoding="utf-8")

    denied = client.get("/frontier", headers={"x-forwarded-for": "192.168.1.50"})
    allowed = client.get(
        "/frontier?token=secret-token",
        headers={"x-forwarded-for": "192.168.1.50"},
    )

    assert denied.status_code == 401
    assert denied.json()["detail"] == "missing or invalid bearer token"
    assert allowed.status_code == 200
    assert "frontier ok" in allowed.text
