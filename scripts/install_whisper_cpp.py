"""Download and extract a prebuilt whisper.cpp release for the current platform.

Picks a release asset from ggerganov/whisper.cpp that matches this machine:
  - Windows x64 + NVIDIA  -> whisper-cublas-<cuda>-bin-x64.zip
  - macOS arm64           -> whisper-bin-arm64.zip   (Metal build)

Extracts into vendor/whisper.cpp/ at the project root, then renames the
primary binary to `whisper-server[.exe]` (upstream ships it as `server[.exe]`).
Idempotent: if the binary already runs, exits fast.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import (  # noqa: E402
    InstallError,
    detect_cuda_arch,
    download,
    extract,
    flatten_if_nested,
    no_window_flags,
)

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = PROJECT_ROOT / "vendor" / "whisper.cpp"

# Pinned to a known-good tag rather than floating "latest" so the feature
# set is deterministic (issue #91). v1.8.5 (2026-05-29, ggml-org/whisper.cpp
# #3781) added server-side `carry_initial_prompt`; v1.8.6 is the newest
# patch on that line. Bump this tag deliberately when a newer one is
# vetted. The repo moved ggerganov → ggml-org; the API redirects either way.
PINNED_TAG = "v1.8.6"
RELEASES_URL = (
    f"https://api.github.com/repos/ggml-org/whisper.cpp/releases/tags/{PINNED_TAG}"
)

# Prefer the newest CUDA line upstream ships; fall back to older ones.
WIN_CUDA_PREFS = ["cublas-12.4.0", "cublas-12.2.0", "cublas-11.8.0"]


def _server_binary() -> Path:
    name = "whisper-server.exe" if sys.platform == "win32" else "whisper-server"
    return VENDOR_DIR / name


def _upstream_server_names() -> List[str]:
    # Upstream has shipped this binary under a couple of names over time.
    if sys.platform == "win32":
        return ["whisper-server.exe", "server.exe"]
    return ["whisper-server", "server"]


def already_installed() -> bool:
    bin_path = _server_binary()
    if not bin_path.exists():
        return False
    # The first exec immediately after a --force extract can transiently
    # fail (Errno-ish / AV scan) while the OS finishes flushing the large
    # CUDA DLLs (cublasLt64_12.dll is ~450 MB) to disk. Retry a couple of
    # times before declaring the binary non-runnable.
    for attempt in range(3):
        try:
            # whisper-server prints usage on --help and exits non-zero, so just
            # check that the binary can execute at all.
            r = subprocess.run([str(bin_path), "--help"],
                               capture_output=True, text=True, timeout=10,
                               creationflags=no_window_flags())
            if r.returncode in (0, 1):
                return True
        except Exception:
            pass
        if attempt < 2:
            time.sleep(1.5)
    return False


def _linux_cuda_build_hint() -> str:
    """A reproducible from-source CUDA build recipe for a Linux satellite.

    Upstream ships no prebuilt Linux CUDA whisper-server; gaming's sm_61 build
    was compiled by hand (#368). Rather than an untested automated compile,
    surface the exact commands with the arch defaulted to this host's detected
    GPU (override via ``LOCAL_LLM_HUB_CUDA_ARCH``). Pinned tag matches the
    Windows/macOS vendored line. The automated build itself is a follow-up.
    """
    arch = os.environ.get("LOCAL_LLM_HUB_CUDA_ARCH") or detect_cuda_arch() or "61"
    return (
        "no prebuilt whisper.cpp asset for Linux — build from source with CUDA.\n"
        f"target GPU arch: sm_{arch} (override via LOCAL_LLM_HUB_CUDA_ARCH). "
        "Reproducible build (run on the satellite; not yet automated — #368):\n"
        f"  git clone --branch {PINNED_TAG} --depth 1 "
        "https://github.com/ggml-org/whisper.cpp /tmp/whisper.cpp\n"
        "  cmake -S /tmp/whisper.cpp -B /tmp/whisper.cpp/build "
        f"-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES={arch} -DWHISPER_BUILD_SERVER=ON\n"
        "  cmake --build /tmp/whisper.cpp/build --config Release -j --target whisper-server\n"
        f"  cp /tmp/whisper.cpp/build/bin/whisper-server {VENDOR_DIR}/"
    )


def _fetch_release() -> dict:
    log.info("querying %s ...", RELEASES_URL)
    req = urllib.request.Request(RELEASES_URL, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _purge_vendor() -> None:
    """Remove the existing vendored tree so a forced reinstall lands clean.

    Used by ``--force`` to upgrade the pinned binary. On Windows the
    server .exe / DLLs are locked while whisper-server is running, so
    rmtree raises — surface that as a clear "stop the server first"
    message rather than a bare OSError.
    """
    if not VENDOR_DIR.exists():
        return
    log.info("--force: removing existing %s", VENDOR_DIR)
    try:
        shutil.rmtree(VENDOR_DIR)
    except (PermissionError, OSError) as exc:
        raise InstallError(
            f"could not remove {VENDOR_DIR} ({exc}). whisper-server is "
            "likely still running and holding the binary. Stop it first "
            "(coordinate with voice-transcriber on the shared :8090/:8091 "
            "mutex), then re-run with --force."
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
        for cuda in WIN_CUDA_PREFS:
            pick = find(lambda n, c=cuda: n.startswith("whisper-") and c in n and "x64" in n and n.endswith(".zip"))
            if pick:
                return [pick]
        raise InstallError(
            f"no CUDA Windows asset in release {release.get('tag_name')}. "
            f"assets available: {names}"
        )

    if sys.platform == "darwin":
        if platform.machine() != "arm64":
            raise InstallError(
                f"only darwin arm64 is supported; this is {platform.machine()}"
            )
        pick = find(lambda n: n.startswith("whisper-") and "arm64" in n and n.endswith((".zip", ".tar.gz")))
        if not pick:
            raise InstallError(
                f"no macOS arm64 asset in release {release.get('tag_name')}. assets: {names}"
            )
        return [pick]

    raise InstallError(f"unsupported platform: {sys.platform}")


def _normalise_binary_name() -> None:
    """Upstream names the server binary `server[.exe]`; the manager expects
    `whisper-server[.exe]`. Rename it if we find the upstream name."""
    want = _server_binary()
    if want.exists():
        return
    for candidate_name in _upstream_server_names():
        for candidate in VENDOR_DIR.rglob(candidate_name):
            # Lift the entire bin directory up alongside the expected path,
            # so sibling DLLs (cudart, whisper.dll, ggml.dll, ...) travel with it.
            src_dir = candidate.parent
            if src_dir != VENDOR_DIR:
                log.info("flattening %s -> %s", src_dir, VENDOR_DIR)
                for child in list(src_dir.iterdir()):
                    target = VENDOR_DIR / child.name
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                    shutil.move(str(child), str(target))
            src = VENDOR_DIR / candidate_name
            if src.exists() and src != want:
                log.info("renaming %s -> %s", src.name, want.name)
                src.rename(want)
            return


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in args

    if force:
        _purge_vendor()
    elif already_installed():
        log.info("whisper.cpp already installed at %s", _server_binary())
        return 0

    if sys.platform.startswith("linux"):
        # A hand-built binary already present is caught by already_installed()
        # above; reaching here means it's missing (or --force purged it) and
        # must be compiled from source.
        raise InstallError(_linux_cuda_build_hint())

    release = _fetch_release()
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
    _normalise_binary_name()

    if not already_installed():
        raise InstallError(
            f"extracted archives but {_server_binary()} still missing or non-runnable"
        )

    log.info("installed: %s", _server_binary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
