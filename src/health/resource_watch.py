"""The resource-watch loop: sample every minute, keep history, judge, alert.

Runs beside the process health watch (``watch.py``) but on its own faster
cadence, because a disk stall lasts minutes where a handle leak takes hours.
Each tick takes one cheap sample (``resource_probe``), appends it to the
SQLite history (``resource_store``), judges the newest samples
(``resource_rules``) and sends any due alert (``resource_alert``). The
verdict and the weekly per-drive stall summary are cached for the endpoints,
so a request never touches the database.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from . import resource_rules as rules, settings
from .resource_alert import AlertGate, Sender
from .resource_probe import ResourceProbe
from .resource_store import ResourceStore

logger = logging.getLogger(__name__)

_MAX_EVENTS = 20
# Enough recent samples in memory to judge any sustained condition without the database.
_RECENT_SAMPLES = 60


def _host_label() -> str:
    """The machine's id in the alert line, so an alert says which box it is about."""
    try:
        from ..host_profile import resolve
        return resolve().id
    except Exception:  # noqa: BLE001 — an unresolvable host must not stop the alert
        return "hub"


class ResourceWatch:
    """Latest verdict, weekly summary and recent alert events. Thread-safe: the
    sampler thread writes, request handlers read."""

    def __init__(self, probe: Optional[ResourceProbe] = None, store: Optional[ResourceStore] = None,
                 send: Optional[Sender] = None) -> None:
        self._lock = threading.Lock()
        self._probe = probe or ResourceProbe()
        self._store = store or ResourceStore()
        self._gate = AlertGate(self._store, send)
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._recent: Deque[Dict[str, Any]] = deque(maxlen=_RECENT_SAMPLES)
            self._events: Deque[Dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
            self._conditions: List[Dict[str, Any]] = []
            self._stalls: Dict[str, Any] = {}
            self._store_state = "ok"
            self._cfg: Dict[str, Any] = settings.DEFAULTS["resource"]

    def prime(self) -> None:
        self._probe.prime()

    def tick(self, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Sample, store, judge and alert once. Blocking: run it in a thread."""
        cfg = (cfg or settings.load())["resource"]
        sample = self._probe.sample()
        try:
            self._store.add_sample(sample, float(cfg["history_days"]))
            store_state = "ok"
        except (sqlite3.Error, OSError) as exc:
            logger.warning("⚠️ resource watch: history not saved: %s", exc)
            store_state = "failed"
        with self._lock:
            self._recent.append(sample)
            history = list(self._recent)
        conditions = rules.evaluate(history, cfg)
        events = self._gate.process(conditions, cfg["alert"], now=sample["ts"], host_label=_host_label())
        stalls = self._stall_summary(cfg, sample, store_state)
        with self._lock:
            self._cfg, self._conditions, self._stalls, self._store_state = cfg, conditions, stalls, store_state
            self._events.extend(events)
        return self.current(now=sample["ts"])

    def _stall_summary(self, cfg: Dict[str, Any], sample: Dict[str, Any], store_state: str) -> Dict[str, Any]:
        """The weekly per-drive stall summary, or ``unknown`` when the history cannot be read."""
        window_days, now = float(cfg["summary_days"]), sample["ts"]
        try:
            samples = self._store.samples_since(now - window_days * 86400.0)
        except (sqlite3.Error, OSError) as exc:
            logger.warning("⚠️ resource watch: history not readable: %s", exc)
            samples, store_state = [], "failed"
        if store_state == "failed" or not samples:
            return {"state": "unknown", "window_days": window_days, "drives": [],
                    "line": "stall history not available"}
        drives = [rules.drive_summary(label, series, cfg, now=now, interval_s=float(cfg["interval_s"]),
                                      window_days=window_days)
                  for label, series in sorted(rules.drive_series(samples).items())]
        measured = [d for d in drives if d["state"] != "unsupported"]
        state = "unknown" if any(d["state"] == "unknown" for d in measured) else "ok"
        if not measured:
            state = "unsupported"
        return {"state": state, "window_days": window_days, "drives": drives,
                "line": "; ".join(d["line"] for d in drives) or "no fixed drives found"}

    def current(self, now: Optional[float] = None) -> Dict[str, Any]:
        """The latest verdict, ``unknown`` when there is no sample or it is stale."""
        now = time.time() if now is None else now
        with self._lock:
            latest = self._recent[-1] if self._recent else None
            cfg, conditions, stalls = self._cfg, list(self._conditions), dict(self._stalls)
            events, store_state = list(self._events), self._store_state
        base = {"enabled": bool(cfg.get("enabled", True)), "interval_s": cfg["interval_s"],
                "store": store_state, "alerts": events}
        if latest is None:
            return {**base, "state": "unknown", "sampled_at": None, "reason": "no sample yet",
                    "conditions": [], "ram": None, "drives": [], "stalls": stalls}
        stale = now - latest["ts"] > float(cfg["interval_s"]) * float(cfg["stale_after_intervals"])
        state = "unknown" if stale else rules.overall(conditions)
        firing = [c for c in conditions if c["state"] in ("alert", "unknown")]
        reason = ("last sample is stale" if stale
                  else "; ".join(c["reason"] for c in sorted(firing, key=lambda c: c["state"] != "alert")[:3]) or "all clear")
        return {**base, "state": state, "sampled_at": latest["ts"], "reason": reason,
                "conditions": conditions, "ram": latest["ram"], "stalls": stalls,
                "drives": [_drive_view(label, row, cfg) for label, row in latest["drives"].items()]}

    def card_summary(self) -> Dict[str, Any]:
        """The few fields the Machines card shows."""
        cur = self.current()
        return {"state": cur["state"], "enabled": cur["enabled"], "sampled_at": cur["sampled_at"],
                "reason": cur["reason"], "stalls": {"state": cur["stalls"].get("state", "unknown"),
                                                    "line": cur["stalls"].get("line", "stall history not available"),
                                                    "drives": [{"drive": d["drive"], "state": d["state"], "line": d["line"]}
                                                               for d in cur["stalls"].get("drives", [])]}}


def _drive_view(label: str, row: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    stall = rules.stall_ms(row, int(cfg["disk_latency"]["min_ios"]))
    return {"drive": label, "disk": row.get("disk"), "free_state": row.get("free"),
            "free_gb": row.get("free_gb"), "total_gb": row.get("total_gb"),
            "latency_state": row.get("lat"), "read_ms": row.get("read_ms"), "write_ms": row.get("write_ms"),
            "stall_ms": stall}


WATCH = ResourceWatch()


async def run_loop() -> None:
    """Sample forever on the configured cadence; never raises out of a tick."""
    cfg = await asyncio.to_thread(settings.load)
    WATCH.prime()
    if not cfg["resource"].get("enabled", True):
        logger.info("ℹ️ resource watch: disabled in config/health_watch.json")
        return
    await asyncio.sleep(float(cfg["resource"]["boot_delay_s"]))
    first = True
    while True:
        started = time.monotonic()
        try:
            cfg = await asyncio.to_thread(settings.load)
            out = await asyncio.to_thread(WATCH.tick, cfg)
            (logger.info if first else logger.debug)("ℹ️ resource watch: %s (%s)", out["state"], out["reason"])
            first = False
        except Exception as exc:  # noqa: BLE001 — a failed tick must not end the watch
            logger.warning("⚠️ resource watch: sample failed: %s", exc)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1.0, float(cfg["resource"]["interval_s"]) - elapsed))
