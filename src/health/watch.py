"""The resident health-watch loop, its rolling history and its alerts.

One sample every ``interval_s`` (10 min by default), taken and judged in a
worker thread so the event loop never waits on it. The alert is the hub log:
a warning when the verdict enters ``act`` and an info line when it leaves,
once each per episode, and the last few are kept for the endpoint so the
launcher can show them.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

from . import classifier, sampler, settings

logger = logging.getLogger(__name__)

_MAX_ALERTS = 20


class HealthWatch:
    """History + latest verdict for this machine. Thread-safe: the sampler
    thread writes, the request handlers read."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: Deque[Dict[str, Any]] = deque()
        self._verdict: Optional[Dict[str, Any]] = None
        self._cfg: Dict[str, Any] = settings.DEFAULTS
        self._alerts: Deque[Dict[str, Any]] = deque(maxlen=_MAX_ALERTS)
        self._in_act = False

    def reset(self) -> None:
        with self._lock:
            self._history.clear()
            self._verdict = None
            self._alerts.clear()
            self._in_act = False
            self._cfg = settings.DEFAULTS

    def configure(self, cfg: Dict[str, Any]) -> None:
        with self._lock:
            self._cfg = cfg

    def record(self, sample: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Add one sample, re-judge, and alert on a move into or out of ``act``."""
        with self._lock:
            self._cfg = cfg
            self._history.append(sample)
            cutoff = sample["ts"] - float(cfg["history_hours"]) * 3600
            while self._history and self._history[0]["ts"] < cutoff:
                self._history.popleft()
            verdict = classifier.classify(list(self._history), cfg, now=sample["ts"])
            self._verdict = verdict
            self._alert_on_transition(verdict)
            return verdict

    def _alert_on_transition(self, verdict: Dict[str, Any]) -> None:
        if verdict["verdict"] == "act" and not self._in_act:
            self._in_act = True
            self._alerts.append({"ts": verdict["sampled_at"], "kind": "act",
                                 "reason": verdict["reason"], "fix": verdict["fix"]})
            logger.warning("🚨 health watch: act: %s. Suggested: %s", verdict["reason"], verdict["fix"])
        elif verdict["verdict"] != "act" and self._in_act:
            self._in_act = False
            self._alerts.append({"ts": verdict["sampled_at"], "kind": "cleared",
                                 "reason": verdict["reason"], "fix": None})
            logger.info("✅ health watch: cleared, now %s: %s", verdict["verdict"], verdict["reason"])

    def current(self, now: Optional[float] = None) -> Dict[str, Any]:
        """The latest verdict, turned ``unknown`` when it is missing or stale."""
        now = time.time() if now is None else now
        with self._lock:
            verdict, cfg = self._verdict, self._cfg
            history = list(self._history)
            alerts = list(self._alerts)
        if verdict is None or not history:
            out = classifier.classify([], cfg, now)
        elif now - history[-1]["ts"] > float(cfg["interval_s"]) * float(cfg["stale_after_intervals"]):
            out = classifier.classify(history[-1:], cfg, now)
        else:
            out = dict(verdict)
        out.update(enabled=bool(cfg.get("enabled", True)), interval_s=cfg["interval_s"],
                   samples=len(history), alerts=alerts)
        return out

    def card_summary(self) -> Dict[str, Any]:
        """The few fields the Machines card shows."""
        v = self.current()
        summary = {k: v[k] for k in ("verdict", "reason", "fix", "sampled_at", "enabled")}
        summary["findings"] = [{k: f.get(k) for k in ("level", "kind", "reason", "fix")} for f in v["findings"][:5]]
        return summary


WATCH = HealthWatch()


def sample_once(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Take one sample and record it. Blocking: run it in a thread."""
    cfg = cfg or settings.load()
    sample = sampler.take_sample(top_n=int(cfg["top_n"]), ignore=list(cfg["ignore_processes"]))
    return WATCH.record(sample, cfg)


async def run_loop() -> None:
    """Sample forever on the configured cadence; never raises out of a tick."""
    cfg = await asyncio.to_thread(settings.load)
    WATCH.configure(cfg)
    if not cfg.get("enabled", True):
        logger.info("ℹ️ health watch: disabled in config/health_watch.json")
        return
    await asyncio.sleep(float(cfg["boot_delay_s"]))
    first = True
    while True:
        started = time.monotonic()
        try:
            cfg = await asyncio.to_thread(settings.load)
            verdict = await asyncio.to_thread(sample_once, cfg)
            log = logger.info if first else logger.debug
            log("ℹ️ health watch: %s (%s), sample took %s ms",
                verdict["verdict"], verdict["reason"], verdict["sample_ms"])
            first = False
        except Exception as exc:  # noqa: BLE001 — a failed tick must not end the watch
            logger.warning("⚠️ health watch: sample failed: %s", exc)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1.0, float(cfg["interval_s"]) - elapsed))
