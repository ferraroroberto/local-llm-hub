"""Read the owner-armed socket trace for the seconds before a Tcpip 4231/4266.

A burst of sockets that are bound and closed without connecting can exhaust
the dynamic range in under a second and leave no TIME_WAIT behind, so neither
the 30 s sample nor the 1 s window sees it. A kernel trace with process ids
can. Arming that trace needs an elevated shell, so the owner runs
``scripts/port_trace.ps1 -Arm`` once: it starts a size-capped circular ETW
session on the Winsock AFD and TCPIP providers and registers a SYSTEM task
that, on each 4231/4266, stops the session, moves its file into this folder as
``trace-<UTC stamp>.etl`` and starts it again.

This module only reads what that task saved. It never starts, stops or
flushes a trace session, and the hub cannot: the session belongs to an
administrator. For a new event it finds the first saved file stamped at or
after the event and counts each process's socket creates, binds, closes and
port reservations in the ``trace_window_s`` before it, with one ``wevtutil``
query that reads only those events. Every state other than ``ok`` says the
holder is not confirmed; none of them is an all-clear.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from calendar import timegm
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..no_window import NO_WINDOW
from .port_probe import EVENT_NS
from .port_store import PortStore

logger = logging.getLogger(__name__)

MARKER = "armed.json"
SAVED_GLOB = "trace-*.etl"
_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_QUERY_TIMEOUT_S = 120

_AFD = "microsoft-windows-winsock-afd"
_TCPIP = "microsoft-windows-tcpip"
_PROVIDER_GUIDS = {"{e53c6823-7bb8-44bb-90dc-3f86090d48a6}": _AFD,
                   "{2f07e2ee-15db-40f1-90ef-9d7ba282188a}": _TCPIP}
# (provider, event id) -> what the event counts as. Binds and closes come from AFD alone, which sees
# TCP and UDP alike; TCPIP adds only the port reservations, which AFD cannot see.
KINDS: Dict[Tuple[str, int], str] = {
    (_AFD, 1000): "create",
    (_AFD, 1001): "close",
    (_AFD, 1029): "bind",
    (_AFD, 1030): "bind",
    (_TCPIP, 1052): "reserve",
    (_TCPIP, 1053): "reserve_failed",
    (_TCPIP, 1054): "reserve",
}
COUNTS = ("create", "bind", "close", "reserve", "reserve_failed")



class TraceState(str, Enum):
    """What the trace could tell about one event; the value is what the stored event keeps.
    Only ``PENDING`` moves on: each event-log read re-reads it until the save lands (``OK``,
    ``FAILED``) or ``trace_wait_s`` passes (``NOT_SAVED``). The rest are final."""

    OK = "ok"
    NOT_ARMED = "not_armed"
    UNSUPPORTED = "unsupported"
    PENDING = "pending"
    NOT_SAVED = "not_saved"
    FAILED = "failed"


# What a query returns: the XML wevtutil printed, or None when the read failed.
Query = Callable[[Path, float, float, int], Optional[str]]
NameOf = Callable[[int], str]
LabelOf = Callable[[int], Optional[str]]


def default_dir(store: Optional[PortStore] = None) -> Path:
    """Beside the port watch's own database, so a test's temp store gets a temp trace folder."""
    return (store or PortStore()).path.parent / "port_trace"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int((ts % 1) * 1000):03d}Z"


def query_etl(path: Path, start: float, end: float, max_events: int) -> Optional[str]:
    """The counted events in ``path`` between ``start`` and ``end`` (UTC epoch), as wevtutil XML.
    The provider is checked by the parser, so the query filters on event id and time only."""
    ids = " or ".join(f"EventID={i}" for i in sorted({event_id for _, event_id in KINDS}))
    query = f"*[System[({ids}) and TimeCreated[@SystemTime>='{_iso(start)}' and @SystemTime<='{_iso(end)}']]]"
    try:
        result = subprocess.run(
            ["wevtutil", "qe", str(path), "/lf:true", f"/q:{query}", "/f:xml", f"/c:{max_events}"],
            capture_output=True, text=True, encoding="oem", errors="replace",
            timeout=_QUERY_TIMEOUT_S, creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        logger.info("ℹ️ port trace: reading %s timed out after %s s", path.name, _QUERY_TIMEOUT_S)
        return None
    except OSError as exc:
        logger.info("ℹ️ port trace: reading %s failed to start: %s", path.name, exc)
        return None
    if result.returncode != 0:
        logger.info("ℹ️ port trace: reading %s exited %s: %s", path.name, result.returncode,
                    result.stderr.strip()[:200])
        return None
    return result.stdout


def parse_counts(xml_text: str) -> Tuple[Dict[int, Dict[str, int]], int]:
    """Per-process counts of each kind, and how many events were counted in all."""
    per_pid: Dict[int, Dict[str, int]] = {}
    total = 0
    root = ET.fromstring(f"<Events>{xml_text}</Events>")
    for node in root.iter(f"{EVENT_NS}Event"):
        system = node.find(f"{EVENT_NS}System")
        if system is None:
            continue
        provider = system.find(f"{EVENT_NS}Provider")
        execution = system.find(f"{EVENT_NS}Execution")
        if provider is None or execution is None:
            continue
        name = (provider.get("Name") or "").lower() or _PROVIDER_GUIDS.get((provider.get("Guid") or "").lower(), "")
        kind = KINDS.get((name, int(system.findtext(f"{EVENT_NS}EventID") or 0)))
        pid = int(execution.get("ProcessID") or 0)
        if kind is None or not pid:
            continue
        per_pid.setdefault(pid, dict.fromkeys(COUNTS, 0))[kind] += 1
        total += 1
    return per_pid, total


class PortTrace:
    """Attributes an exhaustion event from the saved trace. The folder and the query are
    injectable so a test drives every state without a trace session."""

    def __init__(self, trace_dir: Optional[Path] = None, query: Optional[Query] = None) -> None:
        self._dir = trace_dir
        self._query = query or (query_etl if sys.platform == "win32" else None)

    @property
    def folder(self) -> Path:
        return self._dir or default_dir()

    def _armed_at(self) -> Optional[float]:
        """When the owner armed the trace, or ``None`` when no readable marker says it is armed."""
        try:
            data = json.loads((self.folder / MARKER).read_text(encoding="utf-8-sig"))
            return float(data["armed_at"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.info("ℹ️ port trace: marker unreadable, treated as not armed: %s", exc)
            return None

    def _saved_after(self, ts: float) -> Optional[Path]:
        """The first saved trace stamped at or after ``ts``: the save the event itself triggered."""
        found: List[Tuple[float, Path]] = []
        for path in self.folder.glob(SAVED_GLOB):
            try:
                stamp = time.strptime(path.stem[len("trace-"):], _STAMP_FORMAT)
            except ValueError:
                continue
            saved = float(timegm(stamp))
            if saved >= ts:
                found.append((saved, path))
        return min(found)[1] if found else None

    def attribute(self, event: Dict[str, Any], *, now: float, ports: Dict[str, Any],
                  name_of: NameOf, label_of: LabelOf) -> Dict[str, Any]:
        """The ``trace`` block for ``event``. Blocking: call it off the event loop."""
        window = float(ports["trace_window_s"])
        out: Dict[str, Any] = {"from": event["ts"] - window, "to": event["ts"], "checked_at": now}
        armed_at = self._armed_at()
        if armed_at is None or armed_at > event["ts"]:
            return {**out, "state": TraceState.NOT_ARMED.value}
        if self._query is None:
            return {**out, "state": TraceState.UNSUPPORTED.value}
        path = self._saved_after(event["ts"])
        if path is None:
            waited = now - event["ts"]
            state = TraceState.PENDING if waited < float(ports["trace_wait_s"]) else TraceState.NOT_SAVED
            return {**out, "state": state.value}
        xml_text = self._query(path, out["from"], out["to"], int(ports["trace_max_events"]))
        if xml_text is None:
            return {**out, "state": TraceState.FAILED.value, "file": path.name}
        try:
            per_pid, total = parse_counts(xml_text)
        except (ET.ParseError, ValueError) as exc:
            logger.info("ℹ️ port trace: %s output unreadable: %s", path.name, exc)
            return {**out, "state": TraceState.FAILED.value, "file": path.name}
        ranked = sorted(per_pid.items(), key=lambda kv: sum(kv[1].values()), reverse=True)
        holders = [{"pid": pid, "name": name_of(pid), "label": label_of(pid), **counts,
                    "total": sum(counts.values())} for pid, counts in ranked[:int(ports["top_n"])]]
        return {**out, "state": TraceState.OK.value, "file": path.name, "events": total,
                "truncated": total >= int(ports["trace_max_events"]), "holders": holders}


if __name__ == "__main__":
    # scripts/port_trace.ps1 -Arm asks where to save traces, so the task and the hub agree on one folder.
    sys.stdout.write(f"{default_dir()}\n")
