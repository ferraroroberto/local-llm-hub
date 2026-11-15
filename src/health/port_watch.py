"""The port watch: a short-cadence port sampler beside the slow health watch.

Ephemeral-port exhaustion builds and drains in minutes, so the health watch's
10-minute sample misses it. This loop samples every ``ports.interval_s``
(30 s), reads the System log's Tcpip exhaustion events every
``ports.events_interval_s``, keeps both on disk (``port_store``) so a restart
or a reboot keeps the incident, and hands its findings to the health verdict
(``watch.HealthWatch.current``). Advice only: it never closes a socket or
touches a process.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

from . import port_probe, port_rules, settings
from .port_probe import PortProbe
from .port_store import PortStore

logger = logging.getLogger(__name__)

EventReader = Callable[[float], Optional[List[Dict[str, Any]]]]


def rules_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The ``ports`` section plus the shared keys the fix wording reads."""
    return {**cfg["ports"], "attribution_share": cfg.get("attribution_share", 0.5),
            "reboot_only_processes": cfg.get("reboot_only_processes", [])}


class PortWatch:
    """Port history, exhaustion events and the latest event-log read state.
    Thread-safe: the sampler thread writes, request handlers read."""

    def __init__(self, probe: Optional[PortProbe] = None, store: Optional[PortStore] = None,
                 read_events: Optional[EventReader] = None) -> None:
        self._lock = threading.Lock()
        self._probe = probe or PortProbe()
        self._store = store or PortStore()
        # The Tcpip events exist only in the Windows System log.
        default_reader = port_probe.read_exhaustion_events if sys.platform == "win32" else None
        self._read_events = read_events or default_reader
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._history: Deque[Dict[str, Any]] = deque()
            self._events: Dict[int, Dict[str, Any]] = {}
            self._events_probe: Optional[str] = None
            self._events_read_at: Optional[float] = None
            self._cfg: Optional[Dict[str, Any]] = None

    def load(self, cfg: Dict[str, Any], now: Optional[float] = None) -> int:
        """Reload the stored history, so the incident before a restart is still judged."""
        now = time.time() if now is None else now
        ports = cfg["ports"]
        samples = self._store.samples_since(now - float(ports["history_hours"]) * 3600)
        events = self._store.events_since(now - float(ports["event_keep_days"]) * 86400)
        with self._lock:
            self._cfg = rules_config(cfg)
            self._history.extend(samples)
            self._events.update({e["record_id"]: e for e in events})
        return len(samples)

    def _due_events(self, ports: Dict[str, Any], now: float) -> Optional[float]:
        """The lookback for an event-log read now, or ``None`` when one is not due yet."""
        last = self._events_read_at
        if last is not None and now - last < float(ports["events_interval_s"]):
            return None
        lookback = float(ports["events_lookback_hours"]) * 3600
        # Overlap the previous read so an event logged during it is not skipped; record ids dedupe.
        return lookback if last is None else min(lookback, now - last + float(ports["events_interval_s"]))

    def tick(self, cfg: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """Take one sample (and read the event log when due), store and keep it. Blocking."""
        ports = cfg["ports"]
        sample = self._probe.sample(top_n=int(ports["top_n"]), now=now)
        now = sample["ts"]
        events: Optional[List[Dict[str, Any]]] = None
        lookback = self._due_events(ports, now)
        if lookback is not None and self._read_events is not None:
            events = self._read_events(lookback)
        try:
            self._store.add_sample(sample, keep_hours=float(ports["history_hours"]))
            if events:
                self._store.add_events(events, now=now, keep_days=float(ports["event_keep_days"]))
        except Exception as exc:  # noqa: BLE001 — a failed write must not lose the in-memory reading
            logger.warning("⚠️ port watch: history write failed: %s", exc)
        with self._lock:
            self._cfg = rules_config(cfg)
            self._history.append(sample)
            cutoff = now - float(ports["history_hours"]) * 3600
            while self._history and self._history[0]["ts"] < cutoff:
                self._history.popleft()
            if lookback is not None:
                self._events_read_at = now
                if self._read_events is None:
                    self._events_probe = "unsupported"
                else:
                    self._events_probe = "failed" if events is None else "ok"
                for e in events or []:
                    if e["record_id"] not in self._events:
                        logger.warning("🚨 port watch: Windows logged %s (Tcpip %s)", e["reason"], e["event_id"])
                    self._events[e["record_id"]] = e
        return sample

    def current(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Findings, offenders and the ``ports`` block; ``None`` until the watch has run."""
        now = time.time() if now is None else now
        with self._lock:
            cfg = self._cfg
            history = list(self._history)
            events = list(self._events.values())
            events_probe = self._events_probe
        if cfg is None:
            return None
        return {
            "findings": port_rules.findings(history, events, cfg, now, events_probe),
            "offenders": port_rules.offenders(history, now),
            "block": port_rules.block(history, events, cfg, now, events_probe),
            "fresh": not port_rules.is_stale(history, cfg, now),
        }


WATCH = PortWatch()


async def run_loop(on_tick: Optional[Callable[[], Any]] = None) -> None:
    """Sample forever on ``ports.interval_s``; ``on_tick`` re-judges the health
    verdict so an ``act`` alert fires within one port interval. Never raises out of a tick."""
    cfg = await asyncio.to_thread(settings.load)
    if not cfg.get("enabled", True) or not cfg["ports"].get("enabled", True):
        logger.info("ℹ️ port watch: disabled in config/health_watch.json")
        return
    try:
        loaded = await asyncio.to_thread(WATCH.load, cfg)
        logger.info("ℹ️ port watch: %s stored samples reloaded", loaded)
    except Exception as exc:  # noqa: BLE001 — an unreadable history must not stop live sampling
        logger.warning("⚠️ port watch: history reload failed: %s", exc)
    await asyncio.sleep(float(cfg["ports"]["boot_delay_s"]))
    first = True
    while True:
        started = time.monotonic()
        try:
            cfg = await asyncio.to_thread(settings.load)
            sample = await asyncio.to_thread(WATCH.tick, cfg)
            if first:
                logger.info("ℹ️ port watch: tcp %s%%, udp %s%%, sample took %s ms",
                            (sample.get("tcp") or {}).get("percent"), (sample.get("udp") or {}).get("percent"),
                            sample["sample_ms"])
                first = False
            if on_tick:
                on_tick()
        except Exception as exc:  # noqa: BLE001 — a failed tick must not end the watch
            logger.warning("⚠️ port watch: sample failed: %s", exc)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1.0, float(cfg["ports"]["interval_s"]) - elapsed))
