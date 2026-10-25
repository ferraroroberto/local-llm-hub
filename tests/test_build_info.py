"""Build-identity contract: the vendored scaffold helper reports ``built_at``
and ``/admin/api/version`` serves that same capture, not a second clock."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src import build_info
from src.process import process_identity
from src import server as server_mod


def test_build_identity_shape() -> None:
    identity = build_info.build_identity()
    assert set(identity) == {"git_sha", "built_at"}
    assert identity["git_sha"]
    assert identity["built_at"]


def test_version_endpoint_serves_the_process_capture() -> None:
    body = TestClient(server_mod.app).get("/admin/api/version").json()
    assert body["git_sha"] == process_identity.git_sha()
    assert body["built_at"] == process_identity.IDENTITY["built_at"]
