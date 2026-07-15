"""Lightweight system resource probes for the admin SPA's Hub tab sparklines.

Exposes RAM and per-GPU snapshots cheap enough to call on a 5-second tick
without caching. Errors are swallowed and surfaced as empty/None values
so the UI can fall back gracefully on hosts without nvidia-smi (e.g. the
Mac mini).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Optional

import psutil

logger = logging.getLogger(__name__)

_NVIDIA_SMI_TIMEOUT_S = 3.0


def ram_stats() -> dict[str, float]:
    """Return system RAM usage as {used_gb, total_gb, percent}."""
    vm = psutil.virtual_memory()
    gib = 1024 ** 3
    return {
        "used_gb": round(vm.used / gib, 2),
        "total_gb": round(vm.total / gib, 2),
        "percent": float(vm.percent),
    }


def cpu_stats() -> dict[str, float]:
    """Return CPU utilization as {percent}.

    Non-blocking form (interval=None) is safe here since the sampler
    already polls every 2s, giving psutil a fresh comparison baseline
    each tick. The first sample after process start may read 0.0.
    """
    return {"percent": float(psutil.cpu_percent(interval=None))}


def gpu_stats() -> list[dict[str, Optional[float]]]:
    """Return per-GPU snapshot via nvidia-smi.

    One dict per GPU: {name, used_mb, total_mb, vram_percent, util_percent}.
    Returns [] if nvidia-smi is missing, errors, or reports nothing.
    """
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=_NVIDIA_SMI_TIMEOUT_S,
            check=True,
            creationflags=_no_window_flag(),
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        logger.debug("nvidia-smi unavailable: %s", exc)
        return []

    gpus: list[dict[str, Optional[float]]] = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        name, used_str, total_str, util_str = parts[:4]
        used_mb = _to_float(used_str)
        total_mb = _to_float(total_str)
        util_percent = _to_float(util_str)
        vram_percent: Optional[float] = None
        if used_mb is not None and total_mb is not None and total_mb > 0:
            vram_percent = round((used_mb / total_mb) * 100.0, 1)
        gpus.append({
            "name": name,
            "used_mb": used_mb,
            "total_mb": total_mb,
            "vram_percent": vram_percent,
            "util_percent": util_percent,
        })
    return gpus


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _no_window_flag() -> int:
    """CREATE_NO_WINDOW on Windows so nvidia-smi doesn't flash a console."""
    if sys.platform == "win32":
        return 0x08000000
    return 0
