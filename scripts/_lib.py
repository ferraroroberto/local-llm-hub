"""Shared helpers for the vendor-binary install scripts.

``install_llama_cpp.py`` and ``install_whisper_cpp.py`` both download a
GitHub release archive, extract it into a ``vendor/`` directory, and
collapse a single-subdir extraction — near line-for-line duplicates before
this module existed (issue #195). Consolidated here so a fix to the
download/extract/flatten logic lands once instead of twice.

Not a package ``__init__`` — a plain sibling module. Each install script
inserts its own directory onto ``sys.path`` before importing this (works
whether the script runs directly as ``python scripts/install_*.py`` or is
imported as ``scripts.install_*`` from ``src/install.py``'s admin "Fix"
dispatch).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)


class InstallError(RuntimeError):
    pass


def no_window_flags() -> int:
    """CREATE_NO_WINDOW on Windows — this also runs from the windowless hub
    when triggered via the admin SPA's "Fix" button (issue #174)."""
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def detect_cuda_arch() -> str:
    """The host GPU's CUDA compute capability as a bare arch (e.g. ``61`` for a
    GTX 1070's sm_61), from ``nvidia-smi --query-gpu=compute_cap``. Empty string
    if nvidia-smi is absent/unreadable — the caller supplies a default. Used by
    the Linux from-source build hints (#368), where the vendored-release path
    upstream ships for Windows/macOS has no Linux equivalent.
    """
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, creationflags=no_window_flags(),
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0].strip().replace(".", "")
    except Exception:  # noqa: BLE001 — best-effort probe
        pass
    return ""


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s", url)
    log.info("       -> %s", dest)
    with urllib.request.urlopen(url, timeout=120) as r:
        total = int(r.headers.get("Content-Length", 0))
        seen = 0
        next_report = 0
        with dest.open("wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                seen += len(chunk)
                if total and seen >= next_report:
                    pct = 100 * seen / total
                    log.info("  %6.1f / %6.1f MB (%5.1f%%)", seen/1_048_576, total/1_048_576, pct)
                    next_report = seen + total // 20
    log.info("  done: %.1f MB", seen/1_048_576)


def extract(archive: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    log.info("extracting %s -> %s", archive.name, dest_dir)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest_dir)
    elif archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest_dir)
    else:
        raise InstallError(f"unknown archive type: {archive}")


def flatten_if_nested(target: Path) -> None:
    """Some releases extract into a single subdir; collapse it into target."""
    entries = [p for p in target.iterdir() if not p.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for child in inner.iterdir():
            shutil.move(str(child), str(target / child.name))
        inner.rmdir()
