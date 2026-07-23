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
import concurrent.futures
import logging
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from src.host_profile import HostProfile

logger = logging.getLogger(__name__)

_TCP_TIMEOUT_S = 2.0
_SSH_CONNECT_TIMEOUT_S = 6
_CACHE_TTL_S = 30.0
_LIVENESS_PORTS = (22, 3389)  # SSH, then RDP
_WARMUP_RETRY_DELAY_S = 0.5   # pause before the one liveness warm-up retry (#333)
# Under MACHINES_POLL_MS (10 s, app_web/static/state.js) so a poll tick always
# sees a fresh-enough probe, while collapsing the Machines tab and the
# fleet-placement grid's otherwise-independent full liveness fans for the
# same host on the same page load (#369).
_LIVENESS_CACHE_TTL_S = 5.0
# Last-known-good *dial address* TTL (#396) — long enough that the peer-connect
# paths (model proxy, SSH ops) don't re-pay a probe per call, short enough that
# recovery back to the LAN path (or over to the tailnet name) lands within half
# a minute of the network changing underneath us.
_DIAL_TTL_S = 30.0


def _no_window_flags() -> int:
    """CREATE_NO_WINDOW on Windows so the per-host SSH poll (run from the
    windowless hub) doesn't flash a console — see issue #174, #317."""
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# host_id -> (expiry_monotonic, stats_or_None)
_cache: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}

# host_id -> (expiry_monotonic, located_address_or_None) — shared by
# is_reachable()/located_address() below. Since #396 it remembers WHICH
# candidate address answered (LAN, or the tailscale name), not just a bool,
# so the Machines card can badge a peer as reached "via tailnet".
_liveness_cache: Dict[str, tuple[float, Optional[str]]] = {}

# host_id -> (expiry_monotonic, address) — the last-known-good address the
# peer-connect paths should dial (#396). Deliberately a separate cache from
# ``_liveness_cache`` (#369) so the two never fight: liveness may expire and
# re-probe on every poll tick while the dial route stays pinned to its
# last-known-good for the longer ``_DIAL_TTL_S``.
_dial_cache: Dict[str, tuple[float, str]] = {}

# host_id -> address currently in use — transition logging only (#396), so the
# LAN→tailnet failover (and the recovery back) is logged once per flip rather
# than once per probe.
_active_route: Dict[str, str] = {}

# Read-only one-liners, validated live against the real peers. Each emits
# `key value` lines; unavailable metrics (e.g. no nvidia-smi) are simply
# omitted and degrade to a missing gauge rather than an error.
_LINUX_STATS_CMD = (
    "echo \"uptime $(awk '{print int($1)}' /proc/uptime)\"\n"
    "echo \"cpu $(vmstat 1 2 | tail -1 | awk '{print 100-$15}')\"\n"
    "free -m | awk '/Mem:/{printf \"mem_total_mb %d\\nmem_used_mb %d\\n\",$2,$3}'\n"
    "df -k / | awk 'NR==2{printf \"disk_total_kb %d\\ndisk_used_kb %d\\n\",$2,$3}'\n"
    # Guarded with `if … then … fi`, not `A && B`: a missing nvidia-smi must
    # leave the script's exit code 0 so `_run_ssh` keeps the (valid) CPU/mem/disk
    # output. With `&&`, an absent nvidia-smi makes the *whole* command exit
    # non-zero and `_run_ssh` discards everything — a Linux peer with no GPU
    # driver (e.g. a fresh Ubuntu box) then shows a wholly blank card (#329).
    "if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi "
    "--query-gpu=name,memory.used,memory.total,utilization.gpu "
    "--format=csv,noheader,nounits | head -1 | awk -F', *' "
    "'{printf \"gpu_name %s\\ngpu_used_mb %s\\ngpu_total_mb %s\\ngpu_util %s\\n\",$1,$2,$3,$4}'; fi"
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


def _probe_port(address: str, port: int) -> bool:
    try:
        with socket.create_connection((address, port), timeout=_TCP_TIMEOUT_S):
            return True
    except OSError:
        return False


def _probe_liveness_ports(address: str) -> bool:
    """One pass over the liveness ports, probed **concurrently**; True as soon
    as any accepts. A fully-unreachable peer previously paid the sum of every
    port's timeout (``len(_LIVENESS_PORTS) * _TCP_TIMEOUT_S`` = 4 s/pass) —
    probing in parallel bounds one pass at the slowest single port (~2 s), so
    the #333 two-pass warm-up costs ~4.5 s instead of ~8.5 s for a peer that's
    genuinely off (#369)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(_LIVENESS_PORTS)) as pool:
        futures = [pool.submit(_probe_port, address, port) for port in _LIVENESS_PORTS]
        return any(f.result() for f in concurrent.futures.as_completed(futures))


def _candidates(host: HostProfile) -> List[str]:
    """Ordered addresses to try for ``host`` (#396): the wired LAN ``address:``
    first (no WireGuard hop while the wire is healthy — an explicit
    non-goal of the fallback), then the ``tailscale:`` MagicDNS name, which is
    NIC-independent and survives a wired→Wi-Fi failover onto a DHCP pool
    address."""
    out: List[str] = []
    if host.address:
        out.append(host.address)
    if host.tailscale and host.tailscale != host.address:
        out.append(host.tailscale)
    return out


def _record_route(host: HostProfile, winner: str) -> None:
    """Log the LAN→tailnet failover — and the recovery back — at info level,
    once per transition (#396). A silent wired failure must be visible in the
    logs, not masked by the fallback working."""
    prev = _active_route.get(host.id)
    if winner == prev:
        return
    _active_route[host.id] = winner
    if host.tailscale and winner == host.tailscale and winner != host.address:
        logger.info(
            "🛰️ peer %s: LAN address %s unreachable — falling back to tailnet %s",
            host.id, host.address, host.tailscale,
        )
    elif prev is not None and prev == host.tailscale:
        logger.info(
            "🔌 peer %s: LAN address %s answers again — dropping tailnet fallback %s",
            host.id, winner, host.tailscale,
        )


def _locate_once(host: HostProfile) -> Optional[str]:
    """One pass over the candidate addresses, LAN first — the first address
    with an accepting liveness port wins; ``None`` when none answers."""
    for addr in _candidates(host):
        if _probe_liveness_ports(addr):
            return addr
    return None


def locate(host: HostProfile) -> Optional[str]:
    """TCP-connect liveness probe — *is the machine on, and at which address?*
    Independent of SSH auth or the hub: returns the first candidate address
    (LAN, then the tailscale name — #396) with an accepting liveness port, or
    ``None`` when the box is genuinely unreachable everywhere.

    A peer whose NIC has idled into power-save (C-state / Wake-on-LAN sleep)
    routinely drops the *first* SYN and answers the next — so a single warm-up
    retry (the first attempt has already begun waking the NIC) keeps a live box
    from flickering to "down" between polls (#333; gaming did exactly this on
    the first probe after idle). A genuinely-off box still returns None, just
    after two passes; probes run concurrently per host, so the extra latency
    never blocks the rest of the fleet."""
    if not _candidates(host):
        return None
    addr = _locate_once(host)
    if addr is None:
        time.sleep(_WARMUP_RETRY_DELAY_S)
        addr = _locate_once(host)
    if addr is not None:
        _record_route(host, addr)
    return addr


def reachable(host: HostProfile) -> bool:
    """Boolean face of :func:`locate` — kept because "is the box on?" is the
    question most callers ask; the address that answered only matters to the
    #396 dial/badge paths."""
    return locate(host) is not None


async def located_address(host: HostProfile) -> Optional[str]:
    """Cached :func:`locate`, keyed by host id — the address the peer is
    currently reachable at (its LAN ``address:``, or its ``tailscale:`` name
    when only the tailnet answers), ``None`` when it's down.

    The Machines tab (``machine_console``) and the fleet-placement grid
    (``app_web/routers/fleet_placement.py``) both probe the same hosts on the
    same page load and again on every poll tick — each paying an independent
    full TCP liveness fan without this cache. A short TTL (under the 10 s
    Machines poll) collapses that duplication while staying fresh enough that
    neither surface visibly lags a real state change (#369). The Machines card
    compares the winner against ``host.tailscale`` to badge "via tailnet"
    (#396)."""
    now = time.monotonic()
    cached = _liveness_cache.get(host.id)
    if cached is not None and cached[0] > now:
        return cached[1]
    result = await asyncio.to_thread(locate, host)
    _liveness_cache[host.id] = (now + _LIVENESS_CACHE_TTL_S, result)
    return result


async def is_reachable(host: HostProfile) -> bool:
    """Cached liveness, keyed by host id (#369) — see :func:`located_address`,
    which this simply booleanizes."""
    return await located_address(host) is not None


# Host ids with a background dial-route refresh currently in flight (#396) —
# dedup guard so a burst of cache-miss dials spawns one probe thread, not one
# per call.
_refresh_inflight: set = set()
_refresh_lock = threading.Lock()


def _refresh_route(host: HostProfile) -> str:
    """Probe the candidate addresses now (blocking, one pass) and pin the
    winner — or the LAN primary when everything is dead, so callers fail with
    exactly the same connect errors as before the fallback existed — as the
    dial route for ``_DIAL_TTL_S``."""
    winner = _locate_once(host)
    if winner is not None:
        _record_route(host, winner)
    addr = winner or _candidates(host)[0]
    _dial_cache[host.id] = (time.monotonic() + _DIAL_TTL_S, addr)
    return addr


def _kick_refresh(host: HostProfile) -> None:
    """Run :func:`_refresh_route` on a daemon thread, at most one per host at
    a time. Fire-and-forget by design: the caller has already been handed the
    stale/best-guess route and must not wait on the probe."""
    with _refresh_lock:
        if host.id in _refresh_inflight:
            return
        _refresh_inflight.add(host.id)

    def _worker() -> None:
        try:
            _refresh_route(host)
        except Exception:  # noqa: BLE001 — a failed probe must never kill the thread loudly
            logger.debug("dial-route refresh for %s failed", host.id, exc_info=True)
        finally:
            with _refresh_lock:
                _refresh_inflight.discard(host.id)

    threading.Thread(target=_worker, daemon=True, name=f"dial-refresh-{host.id}").start()


def dial_address(host: HostProfile, *, wait: bool = False) -> Optional[str]:
    """The address the peer-connect paths (model-proxy upstream, SSH ops,
    remote stats) should dial right now (#396): the LAN ``address:`` while it
    answers, the ``tailscale:`` MagicDNS name when the LAN path is dead.

    * A host with no ``tailscale:`` name (or no fallback distinct from its
      address) short-circuits to today's behavior — its single address, zero
      probing.
    * With both configured, the winner of a liveness-port probe is cached as
      last-known-good for ``_DIAL_TTL_S``; a fresh :func:`located_address`
      result is reused rather than re-probing.
    * When *both* paths are dead the LAN primary is pinned — callers then fail
      with exactly the same connect errors as before the fallback existed.

    ``wait=False`` (the default) **never blocks**: on a cache miss it returns
    the current best guess (last route, else the LAN primary) immediately and
    refreshes the route on a background thread — this is the only safe mode on
    the event loop, where a probe of a dark address would stall every request
    for seconds (a dead peer's connect probes time out, and that latency
    landed on unrelated requests when this first shipped blocking). The
    trade-off is one possibly-stale dial right after a route change, corrected
    within the probe's own duration. ``wait=True`` probes inline and is for
    code already off the loop (SSH worker threads,
    :func:`dial_address_async`)."""
    cands = _candidates(host)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    now = time.monotonic()
    cached = _dial_cache.get(host.id)
    if cached is not None and cached[0] > now:
        return cached[1]
    live = _liveness_cache.get(host.id)
    if live is not None and live[0] > now and live[1] is not None:
        _dial_cache[host.id] = (now + _DIAL_TTL_S, live[1])
        return live[1]
    if wait:
        return _refresh_route(host)
    _kick_refresh(host)
    # Expired-but-known route first, then whatever transition tracking last
    # saw, then the LAN primary — the same answer pre-#396 callers dialed.
    if cached is not None:
        return cached[1]
    return _active_route.get(host.id) or cands[0]


async def dial_address_async(host: HostProfile) -> Optional[str]:
    """:func:`dial_address` with an inline probe, run off the event loop —
    async callers get the *resolved* route (worth the probe's latency) without
    stalling the loop."""
    return await asyncio.to_thread(dial_address, host, wait=True)


def _run_ssh(host: HostProfile, command: str) -> Optional[str]:
    """Run a read-only command over the hub user's own SSH (not the
    forced-command key). Returns stdout, or None on any failure. Dials the
    host's currently-live address (LAN, else tailnet — #396)."""
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={_SSH_CONNECT_TIMEOUT_S}",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{host.ssh_user}@{dial_address(host, wait=True)}",
        command,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_SSH_CONNECT_TIMEOUT_S + 12,
            creationflags=_no_window_flags(),
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
