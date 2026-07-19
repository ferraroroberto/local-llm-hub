"""Remote machine liveness + stats for the Machines console (#309).

Answers two questions the console cares about, both **independent of whether
the hub runs on the peer**:

  * :func:`is_reachable` — *is the machine powered on?* A plain TCP connect to
    a liveness port (SSH / RDP), so a box that is up but not running the hub
    (OpenClaw) still reads as online.
  * :func:`collect` — the *same* CPU / RAM / GPU / disk / uptime snapshot the
    local host shows, gathered over the hub user's **own** passwordless SSH
    (``ssh <user>@<addr> "<read-only one-liner>"``) — deliberately NOT the
    forced-command key (that key only runs the bootstrap/sync dispatcher).
    This same general-SSH channel also carries the destructive reboot/shutdown
    power actions (``remote_bootstrap``, #311); the forced-command key is now
    solely the hub-lifecycle (bootstrap/sync) path.

Per-OS commands emit ``key value`` lines that :func:`_parse` folds into the
same shape as ``machine_console.self_snapshot``'s stats, so a peer card and
the local card render through identical code. Results are briefly cached —
an SSH round-trip per poll tick would be wasteful and noisy.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
import time
from typing import Any, Dict, Optional

from src.host_profile import HostProfile

logger = logging.getLogger(__name__)

_TCP_TIMEOUT_S = 2.0
_SSH_CONNECT_TIMEOUT_S = 6
_CACHE_TTL_S = 20.0
_LIVENESS_PORTS = (22, 3389)  # SSH, then RDP

# host_id -> (expiry_monotonic, stats_or_None)
_cache: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}

# Read-only one-liners, validated live against the real peers. Each emits
# `key value` lines; unavailable metrics (e.g. no nvidia-smi) are simply
# omitted and degrade to a missing gauge rather than an error.
_LINUX_STATS_CMD = (
    "echo \"uptime $(awk '{print int($1)}' /proc/uptime)\"\n"
    "echo \"cpu $(vmstat 1 2 | tail -1 | awk '{print 100-$15}')\"\n"
    "free -m | awk '/Mem:/{printf \"mem_total_mb %d\\nmem_used_mb %d\\n\",$2,$3}'\n"
    "df -k / | awk 'NR==2{printf \"disk_total_kb %d\\ndisk_used_kb %d\\n\",$2,$3}'\n"
    "command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi "
    "--query-gpu=name,memory.used,memory.total,utilization.gpu "
    "--format=csv,noheader,nounits | head -1 | awk -F', *' "
    "'{printf \"gpu_name %s\\ngpu_used_mb %s\\ngpu_total_mb %s\\ngpu_util %s\\n\",$1,$2,$3,$4}'"
)

_DARWIN_STATS_CMD = (
    "echo \"uptime $(( $(date +%s) - $(sysctl -n kern.boottime | awk -F'[ ,]+' '{print $4}') ))\"\n"
    "echo \"cpu $(top -l 2 -n 0 | grep 'CPU usage' | tail -1 | awk '{print 100-$(NF-1)}')\"\n"
    "echo \"mem_total_mb $(( $(sysctl -n hw.memsize)/1048576 ))\"\n"
    "vm_stat | awk '/page size of/{ps=$8} /Pages active/{a=$3} /Pages wired down/{w=$4} "
    "/Pages occupied by compressor/{c=$5} "
    "END{gsub(/\\./,\"\",a);gsub(/\\./,\"\",w);gsub(/\\./,\"\",c); "
    "printf \"mem_used_mb %d\\n\",(a+w+c)*ps/1048576}'\n"
    "df -k / | awk 'NR==2{printf \"disk_total_kb %d\\ndisk_used_kb %d\\n\",$2,$3}'"
)


def _stats_command(platform: str) -> Optional[str]:
    if platform == "linux":
        return _LINUX_STATS_CMD
    if platform == "darwin":
        return _DARWIN_STATS_CMD
    return None


def reachable(host: HostProfile) -> bool:
    """TCP-connect liveness probe — *is the machine on?* Independent of SSH
    auth or the hub: succeeds as soon as any liveness port accepts."""
    if not host.address:
        return False
    for port in _LIVENESS_PORTS:
        try:
            with socket.create_connection((host.address, port), timeout=_TCP_TIMEOUT_S):
                return True
        except OSError:
            continue
    return False


async def is_reachable(host: HostProfile) -> bool:
    return await asyncio.to_thread(reachable, host)


def _run_ssh(host: HostProfile, command: str) -> Optional[str]:
    """Run a read-only command over the hub user's own SSH (not the
    forced-command key). Returns stdout, or None on any failure."""
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={_SSH_CONNECT_TIMEOUT_S}",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{host.ssh_user}@{host.address}",
        command,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_SSH_CONNECT_TIMEOUT_S + 12,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("remote stats ssh to %s failed: %s", host.id, exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _num(kv: Dict[str, str], key: str) -> Optional[float]:
    try:
        return float(kv[key])
    except (KeyError, ValueError):
        return None


def _parse(raw: str) -> Dict[str, Any]:
    """Fold ``key value`` lines into the ``self_snapshot`` stats shape."""
    kv: Dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            kv[parts[0]] = parts[1].strip()

    stats: Dict[str, Any] = {"uptime_seconds": _num(kv, "uptime")}

    cpu = _num(kv, "cpu")
    stats["cpu"] = {"percent": cpu} if cpu is not None else None

    mem_total, mem_used = _num(kv, "mem_total_mb"), _num(kv, "mem_used_mb")
    if mem_total and mem_used is not None:
        stats["ram"] = {
            "used_gb": round(mem_used / 1024, 2),
            "total_gb": round(mem_total / 1024, 2),
            "percent": round(mem_used / mem_total * 100, 1),
        }
    else:
        stats["ram"] = None

    disk_total, disk_used = _num(kv, "disk_total_kb"), _num(kv, "disk_used_kb")
    if disk_total and disk_used is not None:
        gib = 1024 * 1024
        stats["disk"] = {
            "used_gb": round(disk_used / gib, 2),
            "total_gb": round(disk_total / gib, 2),
            "percent": round(disk_used / disk_total * 100, 1),
        }
    else:
        stats["disk"] = None

    gpus = []
    gpu_total = _num(kv, "gpu_total_mb")
    if gpu_total:
        gpu_used = _num(kv, "gpu_used_mb")
        gpus.append({
            "name": kv.get("gpu_name"),
            "used_mb": gpu_used,
            "total_mb": gpu_total,
            "vram_percent": round(gpu_used / gpu_total * 100, 1) if gpu_used is not None else None,
            "util_percent": _num(kv, "gpu_util"),
        })
    stats["gpus"] = gpus
    return stats


async def collect(host: HostProfile) -> Optional[Dict[str, Any]]:
    """The peer's CPU/RAM/GPU/disk/uptime snapshot over general SSH, cached
    briefly. Returns None when the platform is unsupported, the host has no
    SSH target, or the probe fails (caller still has TCP reachability)."""
    command = _stats_command(host.platform)
    if command is None or not host.can_ssh:
        return None
    now = time.monotonic()
    cached = _cache.get(host.id)
    if cached is not None and cached[0] > now:
        return cached[1]
    raw = await asyncio.to_thread(_run_ssh, host, command)
    stats = _parse(raw) if raw else None
    _cache[host.id] = (now + _CACHE_TTL_S, stats)
    return stats
