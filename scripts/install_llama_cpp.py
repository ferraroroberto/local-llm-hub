"""Download and extract a prebuilt llama.cpp release for the current platform.

Picks a release asset that matches this machine:
  - Windows x64 + NVIDIA  -> llama-<tag>-bin-win-cuda-13.1-x64.zip
                             (plus cudart-llama-bin-win-cuda-13.1-x64.zip
                              for the CUDA runtime DLLs)
  - macOS arm64           -> llama-<tag>-bin-macos-arm64.tar.gz

Extracts into vendor/llama.cpp/ at the project root. Idempotent: if
llama-server[.exe] --version already works, it exits fast.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import (  # noqa: E402
    InstallError,
    download,
    extract,
    fetch_release,
    flatten_if_nested,
    linux_cuda_build_hint,
    lift_into,
    no_window_flags,
    server_binary,
)

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = PROJECT_ROOT / "vendor" / "llama.cpp"
RELEASES_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"

# Prefer CUDA 13.1 on Windows (matches current driver/toolkit line; Blackwell
# requires CUDA >=12.8 so the older 12.4 build is the fallback only).
WIN_CUDA_PREFS = ["cuda-13.1", "cuda-12.4"]


def _server_binary() -> Path:
    return server_binary(VENDOR_DIR, "llama-server")


def already_installed() -> bool:
    bin_path = _server_binary()
    if not bin_path.exists():
        return False
    try:
        r = subprocess.run([str(bin_path), "--version"],
                           capture_output=True, text=True, timeout=10,
                           creationflags=no_window_flags())
        return r.returncode == 0
    except Exception:
        return False


def _linux_cuda_build_hint() -> str:
    """Upstream ships **no** prebuilt Linux CUDA llama-server (#368)."""
    return linux_cuda_build_hint(
        name="llama.cpp",
        git_url="https://github.com/ggml-org/llama.cpp",
        binary="llama-server",
        vendor_dir=VENDOR_DIR,
    )


def _pick_assets(release: dict) -> List[dict]:
    assets = release.get("assets") or []
    names = [a["name"] for a in assets]

    def find(predicate) -> Optional[dict]:
        for a in assets:
            if predicate(a["name"].lower()):
                return a
        return None

    if sys.platform == "win32":
        picks: List[dict] = []
        for cuda in WIN_CUDA_PREFS:
            main = find(lambda n, c=cuda: n.startswith("llama-") and c in n and "win" in n and "x64" in n and n.endswith(".zip"))
            if main:
                picks.append(main)
                rt = find(lambda n, c=cuda: n.startswith("cudart-") and c in n and n.endswith(".zip"))
                if rt:
                    picks.append(rt)
                break
        if not picks:
            raise InstallError(
                f"no matching CUDA Windows asset in release {release.get('tag_name')}. "
                f"assets available: {names}"
            )
        return picks

    if sys.platform == "darwin":
        if platform.machine() != "arm64":
            raise InstallError(
                f"only darwin arm64 is supported; this is {platform.machine()}"
            )
        pick = find(lambda n: n.startswith("llama-") and "macos-arm64" in n and n.endswith((".zip", ".tar.gz")) and "kleidiai" not in n)
        if not pick:
            raise InstallError(
                f"no macOS arm64 asset in release {release.get('tag_name')}. assets: {names}"
            )
        return [pick]

    raise InstallError(f"unsupported platform: {sys.platform}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if already_installed():
        log.info("llama.cpp already installed at %s", _server_binary())
        return 0

    if sys.platform.startswith("linux"):
        # A hand-built binary already present is caught by already_installed()
        # above; reaching here means it's missing and must be compiled.
        raise InstallError(_linux_cuda_build_hint())

    release = fetch_release(RELEASES_URL)
    tag = release.get("tag_name", "?")
    assets = _pick_assets(release)
    log.info("release %s: picking %d asset(s)", tag, len(assets))

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    for a in assets:
        archive = VENDOR_DIR / a["name"]
        if not archive.exists():
            download(a["browser_download_url"], archive)
        extract(archive, VENDOR_DIR)
        archive.unlink(missing_ok=True)

    flatten_if_nested(VENDOR_DIR)

    bin_path = _server_binary()
    if not bin_path.exists():
        # Some zips extract into a `build/bin/` or `bin/` subdirectory.
        for candidate in VENDOR_DIR.rglob(bin_path.name):
            # Move the entire bin directory up next to llama-server.exe.
            if candidate.parent != VENDOR_DIR:
                lift_into(candidate.parent, VENDOR_DIR)
            break

    if not already_installed():
        raise InstallError(
            f"extracted archives but {_server_binary()} still missing or non-runnable"
        )

    log.info("installed: %s", _server_binary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
