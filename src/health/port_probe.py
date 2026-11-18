"""One port sample and the Windows exhaustion events, read from the OS's own tables.

A port sample counts, per protocol, the local ports inside that protocol's
dynamic range that some endpoint holds, the TCP sockets in TIME_WAIT, and the
processes holding the most endpoints in the range. The range comes from the
OS (``netsh`` on Windows, ``/proc`` on Linux) because the owner may have
changed it; the 49152-65535 default stands in only when it cannot be read.

The System log's Tcpip events are ground truth: Windows writes 4227, 4231 or
4266 the moment an allocation fails, so a burst that drains between two
samples still leaves its trace there. A new 4231 or 4266 also takes an
immediate snapshot (``PortProbe.snapshot``) that attributes TIME_WAIT sockets,
which have no owning pid, to the local server port they point at and its
listening process. ``PortProbe.tally`` is the cheap per-process count the 1 s
window (``port_window``) records, so a burst that drained before the event was
read is still attributed.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from calendar import timegm
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import psutil

from ..no_window import NO_WINDOW
from . import win_probe

logger = logging.getLogger(__name__)

PROTOCOLS = ("tcp", "udp")
_DEFAULT_RANGE = (49152, 65535)

# Tcpip event id -> (protocol, what it means)
EXHAUSTION_EVENTS: Dict[int, Tuple[str, str]] = {
    4227: ("tcp", "TCP could not reuse a local port still in time wait"),
    4231: ("tcp", "TCP ran out of ephemeral ports"),
    4266: ("udp", "UDP ran out of ephemeral ports"),
}
# The events whose holder the watch snapshots at once: the dynamic range ran out.
SNAPSHOT_EVENTS = (4231, 4266)
_EVENT_NS ="{http://schemas.microsoft.com/win/2004/08/events/event}"
_MAX_EVENTS_PER_READ = 50

Connections = Callable[[str], Iterable[Any]]
NameOf = Callable[[int], str]


def dynamic_range(protocol: str) -> Tuple[int, int]:
    """The dynamic port range of ``protocol`` on this machine."""
    found: Optional[Tuple[int, int]] = None
    if sys.platform == "win32":
        found = win_probe.dynamic_port_range(protocol)
    elif sys.platform.startswith("linux"):
        # Linux has one local port range shared by TCP and UDP.
        try:
            lo, hi = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()
            found = (int(lo), int(hi))
        except (OSError, ValueError):
            found = None
    return found or _DEFAULT_RANGE


def process_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except (psutil.Error, OSError):
        return f"pid {pid}"


class PortProbe:
    """Takes port samples. The endpoint table, the name lookup and the ranges are
    injectable so a test drives a burst without opening a single socket."""

    def __init__(self, connections: Optional[Connections] = None, name_of: Optional[NameOf] = None,
                 ranges: Optional[Dict[str, Tuple[int, int]]] = None) -> None:
        self._connections = connections or psutil.net_connections
        self._name_of = name_of or process_name
        self._ranges = ranges
        self._names: Dict[int, str] = {}

    def ranges(self) -> Dict[str, Tuple[int, int]]:
        """Read once per probe: the range is a reboot-scope machine setting."""
        if self._ranges is None:
            self._ranges = {p: dynamic_range(p) for p in PROTOCOLS}
        return self._ranges

    def _name(self, pid: int) -> str:
        if pid not in self._names:
            self._names[pid] = self._name_of(pid)
        return self._names[pid]

    def _time_wait_groups(self, rows: List[Any], top_n: int) -> Tuple[List[Dict[str, Any]], int]:
        """TIME_WAIT sockets grouped by the local server port they belong to, each with its
        listening process, plus how many sockets no port could place. A TIME_WAIT socket has
        no owning pid, but its burst is visible as many sockets pointing at one server port."""
        listeners = {int(r.laddr.port): int(r.pid) for r in rows
                     if r.status == psutil.CONN_LISTEN and r.laddr and r.pid}
        counts: Dict[Tuple[int, bool], int] = {}
        unplaced = 0
        for row in rows:
            if row.status != psutil.CONN_TIME_WAIT:
                continue
            local = int(row.laddr.port) if row.laddr else None
            remote_addr = getattr(row, "raddr", None)
            remote = int(remote_addr.port) if remote_addr else None
            if local in listeners:
                key = (local, True)
            elif remote in listeners:
                key = (remote, True)
            elif remote is not None:
                key = (remote, False)
            else:
                unplaced += 1
                continue
            counts[key] = counts.get(key, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        groups = []
        for (port, is_local), count in ranked:
            pid = listeners.get(port) if is_local else None
            groups.append({"port": port, "listener": self._name(pid) if pid else None, "pid": pid, "count": count})
        return groups, unplaced

    def snapshot(self, *, top_n: int = 10, now: Optional[float] = None,
                 rank_by: Optional[str] = None) -> Dict[str, Any]:
        """A sample that also attributes TIME_WAIT by port and listener: the moment an
        exhaustion event lands, this names who held the ports. ``rank_by`` orders the
        holders by that protocol's endpoints, so a UDP event leads with its UDP holders.
        Blocking."""
        sample = self.sample(top_n=top_n, now=now, with_time_wait=True, rank_by=rank_by)
        return {key: sample[key] for key in ("ts", "probes", "tcp", "udp", "holders", "time_wait_groups",
                                              "time_wait_unplaced") if key in sample}

    def name_of(self, pid: int) -> str:
        """The process name of ``pid``, for the window that outlives the process."""
        return self._name_of(pid)

    def _read_table(self, protocol: str, probes: Dict[str, str], *, quiet: bool = False) -> Optional[List[Any]]:
        """The endpoint table of ``protocol``, or ``None`` with ``probes[protocol]`` saying why."""
        try:
            rows = list(self._connections(protocol))
        except psutil.AccessDenied:
            probes[protocol] = "denied"
            return None
        except OSError as exc:
            if not quiet:
                logger.info("ℹ️ port watch: %s table read failed: %s", protocol, exc)
            probes[protocol] = "failed"
            return None
        probes[protocol] = "ok"
        return rows

    def tally(self, *, now: Optional[float] = None) -> Dict[str, Any]:
        """The endpoints each process holds in the dynamic ranges and nothing else: the cheap
        read the 1 s burst window records. Blocking: call it off the event loop."""
        ranges = self.ranges()
        per_pid: Dict[int, Dict[str, int]] = {}
        probes: Dict[str, str] = {}
        for protocol in PROTOCOLS:
            rows = self._read_table(protocol, probes, quiet=True)
            if rows is not None:
                lo, hi = ranges[protocol]
                _count_by_pid(per_pid, [r for r in rows if r.laddr and lo <= r.laddr.port <= hi], protocol)
        return {"ts": time.time() if now is None else now, "probes": probes,
                "counts": {pid: (c["tcp"], c["udp"]) for pid, c in per_pid.items()}}

    def sample(self, *, top_n: int = 10, now: Optional[float] = None,
               with_time_wait: bool = False, rank_by: Optional[str] = None) -> Dict[str, Any]:
        """One sample. Blocking: call it off the event loop."""
        started, cpu_started = time.perf_counter(), time.process_time()
        ranges = self.ranges()
        out: Dict[str, Any] = {"ts": time.time() if now is None else now, "probes": {}}
        per_pid: Dict[int, Dict[str, int]] = {}
        for protocol in PROTOCOLS:
            lo, hi = ranges[protocol]
            rows = self._read_table(protocol, out["probes"])
            if rows is None:
                continue
            in_range = [r for r in rows if r.laddr and lo <= r.laddr.port <= hi]
            used = {r.laddr.port for r in in_range}
            _count_by_pid(per_pid, in_range, protocol)
            total = hi - lo + 1
            out[protocol] = {"used": len(used), "total": total, "range": [lo, hi],
                             "percent": round(100.0 * len(used) / total, 1)}
            if protocol == "tcp":
                out[protocol]["time_wait"] = sum(1 for r in rows if r.status == psutil.CONN_TIME_WAIT)
                if with_time_wait:
                    out["time_wait_groups"], out["time_wait_unplaced"] = self._time_wait_groups(rows, top_n)
        live = set(per_pid)
        self._names = {pid: name for pid, name in self._names.items() if pid in live}
        if rank_by in PROTOCOLS:
            # An event of one protocol ranks by that protocol alone; a process holding none of it is no holder.
            ranked = sorted(((pid, c) for pid, c in per_pid.items() if c[rank_by]),
                            key=lambda kv: kv[1][rank_by], reverse=True)
        else:
            ranked = sorted(per_pid.items(), key=lambda kv: kv[1]["tcp"] + kv[1]["udp"], reverse=True)
        out["holders"] = [
            {"pid": pid, "name": self._name(pid), "tcp": c["tcp"], "udp": c["udp"], "ports": c["tcp"] + c["udp"]}
            for pid, c in ranked[:top_n]
        ]
        out["sample_ms"] = round((time.perf_counter() - started) * 1000, 1)
        out["cpu_ms"] = round((time.process_time() - cpu_started) * 1000, 1)
        return out


def _count_by_pid(per_pid: Dict[int, Dict[str, int]], rows: Iterable[Any], protocol: str) -> None:
    """Add the endpoints in ``rows`` to their owning process's ``protocol`` count."""
    for row in rows:
        if row.pid:
            per_pid.setdefault(int(row.pid), {"tcp": 0, "udp": 0})[protocol] += 1


def _parse_events(xml_text: str) -> List[Dict[str, Any]]:
    """Each ``<Event>`` wevtutil printed, as ``record_id``/``ts``/``event_id``/``protocol``/``reason``."""
    events = []
    root = ET.fromstring(f"<Events>{xml_text}</Events>")
    for node in root.iter(f"{_EVENT_NS}Event"):
        system = node.find(f"{_EVENT_NS}System")
        if system is None:
            continue
        event_id = int(system.findtext(f"{_EVENT_NS}EventID") or 0)
        created = system.find(f"{_EVENT_NS}TimeCreated")
        record = system.findtext(f"{_EVENT_NS}EventRecordID")
        if event_id not in EXHAUSTION_EVENTS or created is None or not record:
            continue
        protocol, reason = EXHAUSTION_EVENTS[event_id]
        events.append({"record_id": int(record), "ts": _parse_system_time(created.get("SystemTime", "")),
                       "event_id": event_id, "protocol": protocol, "reason": reason})
    return events


def _parse_system_time(text: str) -> float:
    """``2026-11-15T00:42:27.0919579Z`` -> epoch seconds (UTC)."""
    whole, _, frac = text.rstrip("Z").partition(".")
    seconds = timegm(time.strptime(whole, "%Y-%m-%dT%H:%M:%S"))
    return seconds + (float(f"0.{frac}") if frac.isdigit() else 0.0)


def read_exhaustion_events(lookback_s: float) -> Optional[List[Dict[str, Any]]]:
    """Tcpip exhaustion events from the System log in the last ``lookback_s``,
    newest first; ``[]`` when there are none, ``None`` when the log could not be read."""
    ids = " or ".join(f"EventID={i}" for i in EXHAUSTION_EVENTS)
    query = (f"*[System[Provider[@Name='Tcpip'] and ({ids}) "
             f"and TimeCreated[timediff(@SystemTime) <= {int(lookback_s * 1000)}]]]")
    try:
        result = subprocess.run(
            ["wevtutil", "qe", "System", f"/q:{query}", "/f:xml", f"/c:{_MAX_EVENTS_PER_READ}", "/rd:true"],
            capture_output=True, text=True, encoding="oem", errors="replace",
            timeout=20, creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        logger.info("ℹ️ port watch: event log read timed out after 20 s")
        return None
    except OSError as exc:
        logger.info("ℹ️ port watch: event log read failed to start: %s", exc)
        return None
    if result.returncode != 0:
        logger.info("ℹ️ port watch: event log read exited %s: %s", result.returncode, result.stderr.strip()[:200])
        return None
    try:
        return _parse_events(result.stdout)
    except (ET.ParseError, ValueError) as exc:
        logger.info("ℹ️ port watch: event log output unreadable: %s", exc)
        return None
