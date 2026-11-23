"""The port watch: a short-cadence port sampler beside the slow health watch.

Ephemeral-port exhaustion builds and drains in minutes, so the health watch's
10-minute sample misses it. This loop samples every ``ports.interval_s``
(30 s), tallies per-process endpoints into a burst window (every 1 s under port
pressure, slower otherwise), reads the System log's Tcpip exhaustion events every
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

from src.concurrency.pools import BACKGROUND, run_on
from . import periodic, port_probe, port_rules, settings
from .port_probe import PortProbe
from .port_store import PortStore
from .port_window import PortWindow

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
        self._window = PortWindow()
        self._window_probes: Optional[Dict[str, str]] = None
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

    def record_window(self, ports: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """Add one per-process tally to the rolling burst window. Blocking: call it off the event loop."""
        tally = self._probe.tally(now=now)
        self._window.add(tally, self._probe.name_of, float(ports["window_s"]))
        if tally["probes"] != self._window_probes:  # log a table going unreadable once, not once a second
            self._window_probes = dict(tally["probes"])
            logger.info("ℹ️ port watch: burst window table state %s", tally["probes"])
        return tally

    def _attach_snapshot(self, events: Optional[List[Dict[str, Any]]], ports: Dict[str, Any], now: float) -> None:
        """Snapshot the port holders now for an exhaustion event not seen before, and keep it
        on the event with the window's peak holders: by the next interval the burst has drained.
        One table read per protocol serves every new event of it; each carries its own delay,
        the seconds between the event and the read."""
        with self._lock:
            fresh = [e for e in events or [] if e["event_id"] in port_probe.SNAPSHOT_EVENTS
                     and e["record_id"] not in self._events]
        for e in fresh:
            e["peak"] = self._window.peaks(e["protocol"], event_ts=e["ts"], top_n=int(ports["top_n"]),
                                           min_rise=int(ports["window_min_rise"]))
        snapshots: Dict[str, Dict[str, Any]] = {}
        for protocol in sorted({e["protocol"] for e in fresh}):
            try:
                snapshots[protocol] = self._probe.snapshot(top_n=int(ports["top_n"]), now=now, rank_by=protocol)
            except Exception as exc:  # noqa: BLE001 — a failed snapshot must not lose the event itself
                logger.warning("⚠️ port watch: holder snapshot failed: %s", exc)
        for e in fresh:
            snapshot = snapshots.get(e["protocol"])
            if snapshot:
                e["snapshot"] = {**snapshot, "delay_s": round(max(0.0, snapshot["ts"] - e["ts"]), 1)}
                logger.info("ℹ️ port watch: holder snapshot taken %s s after Tcpip %s", e["snapshot"]["delay_s"],
                            e["event_id"])
            peak = e["peak"]
            logger.info("ℹ️ port watch: Tcpip %s window of %s s, %s readable samples, peak holder %s", e["event_id"],
                        round((peak["to"] or 0) - (peak["from"] or 0)), peak["samples"],
                        f"{peak['holders'][0]['name']} ({peak['holders'][0]['peak']})" if peak["holders"]
                        else "none")

    def tick(self, cfg: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """Take one sample (and read the event log when due), store and keep it. Blocking."""
        ports = cfg["ports"]
        sample = self._probe.sample(top_n=int(ports["top_n"]), now=now)
        now = sample["ts"]
        events: Optional[List[Dict[str, Any]]] = None
        lookback = self._due_events(ports, now)
        if lookback is not None and self._read_events is not None:
            events = self._read_events(lookback)
            self._attach_snapshot(events, ports, now)
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
                    if e["record_id"] in self._events:
                        continue  # a re-read of a known event carries no snapshot; keep the one taken
                    logger.warning("🚨 port watch: Windows logged %s (Tcpip %s)", e["reason"], e["event_id"])
                    self._events[e["record_id"]] = e
        return sample

    def window_interval(self, ports: Dict[str, Any], now: Optional[float] = None) -> float:
        """The burst window's tally interval: ``window_interval_s`` while the newest port sample
        reaches ``window_arm_percent`` of either range (or none is fresh and readable, so pressure
        is unknown), ``window_idle_interval_s`` otherwise. A burst builds over the samples before
        it, so the 1 s tally arms in time without reading both tables every second while idle."""
        now = time.time() if now is None else now
        with self._lock:
            last = self._history[-1] if self._history else None
        fine = float(ports["window_interval_s"])
        if last is None or now - last["ts"] > float(ports["interval_s"]) * float(ports["stale_after_intervals"]):
            return fine
        arm = float(ports["window_arm_percent"])
        for protocol in port_probe.PROTOCOLS:
            percent = (last.get(protocol) or {}).get("percent")
            if percent is None or float(percent) >= arm:
                return fine
        return float(ports["window_idle_interval_s"])

    def ports_config(self) -> Optional[Dict[str, Any]]:
        """The ``ports`` settings the last tick or load saw, ``None`` before either."""
        with self._lock:
            return self._cfg

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
    cfg = await run_on(BACKGROUND, settings.load)
    if not cfg.get("enabled", True) or not cfg["ports"].get("enabled", True):
        logger.info("ℹ️ port watch: disabled in config/health_watch.json")
        return
    try:
        loaded = await run_on(BACKGROUND, WATCH.load, cfg)
        logger.info("ℹ️ port watch: %s stored samples reloaded", loaded)
    except Exception as exc:  # noqa: BLE001 — an unreadable history must not stop live sampling
        logger.warning("⚠️ port watch: history reload failed: %s", exc)
    await asyncio.sleep(float(cfg["ports"]["boot_delay_s"]))
    window_task = asyncio.create_task(run_window_loop(cfg["ports"]))
    try:
        await _sample_forever(cfg, on_tick)
    finally:
        window_task.cancel()


async def run_window_loop(ports: Dict[str, Any]) -> None:
    """Tally the per-process endpoints into the burst window every ``WATCH.window_interval`` (1 s
    under port pressure, ``ports.window_idle_interval_s`` otherwise), in a worker thread so the
    event loop never waits on the table read. Never raises out of a tally; a failing read is
    logged when it starts, not once a second."""
    failing, first = False, True
    while True:
        started = time.monotonic()
        ports = WATCH.ports_config() or ports
        try:
            await run_on(BACKGROUND, WATCH.record_window, ports)
            failing = False
            if first:
                logger.info("ℹ️ port watch: burst window of %s s every %s s (%s s idle), first tally took %.1f ms",
                            ports["window_s"], ports["window_interval_s"], ports["window_idle_interval_s"],
                            (time.monotonic() - started) * 1000)
                first = False
        except Exception as exc:  # noqa: BLE001 — a failed tally must not end the window
            if not failing:
                logger.warning("⚠️ port watch: burst window tally failed: %s", exc)
            failing = True
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.2, WATCH.window_interval(ports) - elapsed))


async def _sample_forever(cfg: Dict[str, Any], on_tick: Optional[Callable[[], Any]]) -> None:
    def _after(sample: Dict[str, Any], first: bool) -> None:
        if first:
            logger.info("ℹ️ port watch: tcp %s%%, udp %s%%, sample took %s ms",
                        (sample.get("tcp") or {}).get("percent"), (sample.get("udp") or {}).get("percent"),
                        sample["sample_ms"])
        if on_tick:
            on_tick()

    await periodic.run_ticks(
        "port watch", cfg, load_cfg=settings.load, tick=WATCH.tick, interval_of=lambda c: c["ports"]["interval_s"],
        floor_s=1.0, on_result=_after)
