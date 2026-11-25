"""A rolling window of per-second, per-process port counts, kept in memory.

A UDP port is free the instant its socket closes, so a burst that exhausts the
range is gone before the 30 s sample or the event-log read looks. This window
keeps the last ``window_s`` seconds of one-second tallies (``PortProbe.tally``)
so that, when Windows logs a 4231 or 4266, the watch can say which process
peaked and when. It never persists a tally: only the peak summary of a new
event is stored, beside that event's snapshot.

A burst shorter than the tally interval can still fall between two reads, so a
window that shows no process rising says the holder is not confirmed rather
than that nothing held the ports.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Tuple

from .port_probe import PROTOCOLS

_PROTOCOL_INDEX = {protocol: i for i, protocol in enumerate(PROTOCOLS)}
NameOf = Callable[[int], str]


class PortWindow:
    """Thread-safe: the 1 s thread adds tallies, the 30 s tick reads a peak summary."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Deque[Tuple[float, Dict[str, str], Dict[int, Tuple[int, int]]]] = deque()
        self._names: Dict[int, str] = {}

    def add(self, tally: Dict[str, Any], name_of: NameOf, window_s: float) -> None:
        """Keep ``tally`` and drop what has aged out of ``window_s``. A name is looked up once
        per pid, here, because by the time an event is read the burst's process may have exited."""
        counts = tally["counts"]
        with self._lock:
            for pid in counts:
                if pid not in self._names:
                    self._names[pid] = name_of(pid)
            self._entries.append((tally["ts"], tally["probes"], counts))
            cutoff = tally["ts"] - window_s
            while self._entries and self._entries[0][0] < cutoff:
                self._entries.popleft()
            live = {pid for _, _, held in self._entries for pid in held}
            self._names = {pid: name for pid, name in self._names.items() if pid in live}

    def peaks(self, protocol: str, *, event_ts: float, top_n: int, range_size: int,
              confirm_percent: float) -> Dict[str, Any]:
        """Who held the most ``protocol`` endpoints in the window, each with the second it peaked
        and its lowest count (``low``, 0 when absent). ``confirmed`` is true only when the window
        reaches back to the event and some process rose by ``confirm_percent`` of ``range_size``
        or more: a process that moved by a sliver of the range cannot have exhausted it, a steady
        holder is a bystander, and an empty or unreadable window confirms nothing. ``largest_rise``
        is the biggest rise of any process, so a small mover is still reported as just that."""
        index = _PROTOCOL_INDEX[protocol]
        min_rise = max(1, math.ceil(range_size * confirm_percent / 100))
        with self._lock:
            entries = list(self._entries)
            names = dict(self._names)
        readable = [(ts, held) for ts, probes, held in entries if probes.get(protocol) == "ok"]
        out: Dict[str, Any] = {
            "protocol": protocol, "samples": len(readable), "unread": len(entries) - len(readable),
            "from": entries[0][0] if entries else None, "to": entries[-1][0] if entries else None,
            "range_size": range_size, "min_rise": min_rise, "largest_rise": 0, "holders": [],
            "confirmed": False,
        }
        out["covers_event"] = bool(entries) and entries[0][0] <= event_ts
        if not readable:
            return out
        stats: Dict[int, List[Any]] = {}  # pid -> [peak, peak_ts, low]
        for ts, held in readable:
            for pid, pair in held.items():
                count = pair[index]
                peak = stats.setdefault(pid, [0, ts, count])
                if count > peak[0]:
                    peak[0], peak[1] = count, ts
                peak[2] = min(peak[2], count)
        # A pid absent from a second held nothing in it, so its low is 0 unless it was always there.
        present_always = {pid for pid in stats if all(pid in held for _, held in readable)}
        rows = [{"pid": pid, "name": names.get(pid, f"pid {pid}"), "peak": peak, "at": at,
                 "low": low if pid in present_always else 0}
                for pid, (peak, at, low) in stats.items() if peak]
        rows.sort(key=lambda r: r["peak"], reverse=True)
        out["largest_rise"] = max((r["peak"] - r["low"] for r in rows), default=0)
        out["confirmed"] = out["covers_event"] and out["largest_rise"] >= min_rise
        out["holders"] = rows[:top_n]
        return out
