"""Which llama.cpp release the installer downloads from.

Driven through ``main()`` over a recorded GitHub releases listing, with only the
HTTP call, the download and the extraction faked: upstream's "latest" release is
a ``vX.Y.Z`` tag carrying no binaries, while the builds ship on the numbered
``bNNNNN`` releases, which are marked as prereleases.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.request
from pathlib import Path
from typing import List

import pytest

from scripts import install_llama_cpp

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "llama_cpp_releases.json"


def _recorded() -> List[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _without(release: dict, fragment: str) -> dict:
    return {**release, "assets": [a for a in release["assets"] if fragment not in a["name"]]}


@pytest.fixture
def run_install(monkeypatch, tmp_path):
    """Run ``main()`` against ``releases``; return the asset names it installed."""

    def _run(releases: List[dict], *, system: str = "win32") -> List[str]:
        binary = tmp_path / "llama-server"
        installed: List[str] = []

        def _urlopen(request, timeout):
            body = releases[0] if request.full_url.endswith("/releases/latest") else releases
            return io.BytesIO(json.dumps(body).encode())

        def _extract(archive, dest_dir):
            installed.append(archive.name)
            binary.touch()

        monkeypatch.setattr(install_llama_cpp.sys, "platform", system)
        monkeypatch.setattr(install_llama_cpp.platform, "machine", lambda: "arm64")
        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        monkeypatch.setattr(install_llama_cpp, "VENDOR_DIR", tmp_path)
        monkeypatch.setattr(install_llama_cpp, "download", lambda url, dest: None)
        monkeypatch.setattr(install_llama_cpp, "extract", _extract)
        monkeypatch.setattr(install_llama_cpp, "_server_binary", lambda: binary)
        monkeypatch.setattr(install_llama_cpp, "already_installed", binary.exists)

        assert install_llama_cpp.main() == 0
        return installed

    return _run


def test_windows_build_comes_from_the_newest_numbered_release(run_install, caplog):
    caplog.set_level(logging.INFO)

    picked = run_install(_recorded())

    assert picked == [
        "llama-b12785-bin-win-cuda-13.5-x64.zip",
        "cudart-llama-bin-win-cuda-13.5-x64.zip",
    ]
    assert "release b12785" in caplog.text
    assert "llama-b12785-bin-win-cuda-13.5-x64.zip" in caplog.text


def test_macos_build_comes_from_the_newest_numbered_release(run_install):
    assert run_install(_recorded(), system="darwin") == ["llama-b12785-bin-macos-arm64.tar.gz"]


@pytest.mark.parametrize("missing", ["bin-win", "cudart-"])
def test_a_release_without_the_full_windows_build_is_passed_over(run_install, missing):
    latest, newest, older = _recorded()

    picked = run_install([latest, _without(newest, missing), older])

    assert picked == [
        "llama-b12784-bin-win-cuda-13.5-x64.zip",
        "cudart-llama-bin-win-cuda-13.5-x64.zip",
    ]


def test_newest_is_decided_by_build_number_not_listing_order(run_install):
    latest, newest, older = _recorded()

    assert run_install([latest, older, newest])[0] == "llama-b12785-bin-win-cuda-13.5-x64.zip"


def test_no_numbered_release_with_a_build_is_its_own_error(run_install):
    latest, newest, older = _recorded()

    with pytest.raises(install_llama_cpp.InstallError, match="in any of the 2 numbered releases"):
        run_install([latest, _without(newest, "bin-win"), _without(older, "bin-win")])
