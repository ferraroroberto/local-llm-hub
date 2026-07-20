"""The on-demand capture loop (issue #315).

One asyncio task inside the already-running hub, alive only while a capture
is. That is the whole anti-bloat contract: when no run is active, this module
holds a ``None`` and costs nothing — no thread, no timer, no resident process.

A run samples every ``interval_s`` until ``duration_s`` elapses or it is
stopped. Sampling itself (psutil scans, ``nvidia-smi``, SQLite writes) is
blocking work, so each tick is dispatched with ``asyncio.to_thread`` and never
stalls the hub's request loop.

Only one run may be active at a time — concurrent captures would double the
observer effect and interleave confusingly in the store.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import socket
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import psutil

from src import system_stats

from . import attribution, store

logger = logging.getLogger(__name__)

# Bounds. A capture is a diagnostic, not a monitoring daemon: a floor on the
# interval stops a pathological request from turning the sampler into the
# load it is meant to measure, and the duration ceiling stops a forgotten run
# from growing the DB forever.
MIN_INTERVAL_S = 5.0
MAX_INTERVAL_S = 600.0
DEFAULT_INTERVAL_S = 15.0
MAX_DURATION_S = 24 * 3600.0
DEFAULT_DURATION_S = 3600.0


@dataclass
class ActiveRun:
    run_id: str
    started_at: float
    interval_s: float
    duration_s: Optional[float]
    trigger: str
    samples_written: int = 0
    last_error: str = ""

    @property
    def deadline(self) -> Optional[float]:
        return self.started_at + self.duration_s if self.duration_s else None

    def as_dict(self) -> Dict[str, Any]:
        now = time.time()
        elapsed = max(0.0, now - self.started_at)
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "interval_s": self.interval_s,
            "duration_s": self.duration_s,
            "trigger": self.trigger,
            "elapsed_s": elapsed,
            "remaining_s": max(0.0, self.duration_s - elapsed) if self.duration_s else None,
            "samples_written": self.samples_written,
            "last_error": self.last_error,
        }


_active: Optional[ActiveRun] = None
_task: Optional[asyncio.Task] = None
_lock = asyncio.Lock()
# Set to ask the loop to finish its current tick and exit (see stop_run).
_stop_requested = asyncio.Event()
# How long a graceful stop waits before falling back to cancellation.
_STOP_GRACE_S = 30.0


async def _sleep_or_stop(seconds: float) -> bool:
    """Sleep, returning early (``True``) if a stop is requested meanwhile."""
    if seconds <= 0:
        return _stop_requested.is_set()
    try:
        await asyncio.wait_for(_stop_requested.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


def active_run() -> Optional[ActiveRun]:
    return _active


def is_capturing() -> bool:
    return _active is not None


# ------------------------------------------------------------- one tick


# System CPU is measured over our own short blocking window rather than
# psutil's interval=None mode. That mode reports usage since the *previous
# call in this process*, and the hub's own Hub-tab resource sampler already
# calls it every 2 s — so whichever of the two ran last stole the other's
# delta and diagnostics reported 0.0% CPU on a genuinely busy box. A private
# window is independent of any other caller. It costs 0.5 s inside the worker
# thread (≤10% of the 5 s interval floor, 3% at the 15 s default) and never
# touches the event loop.
_CPU_WINDOW_S = 0.5


def _collect_tick() -> tuple[store.SystemSample, list, list]:
    """Gather one full sample. Runs in a worker thread (blocking psutil)."""
    processes = attribution.scan_processes()
    ports = attribution.scan_listening_ports(processes)

    try:
        per_core = [float(x) for x in psutil.cpu_percent(interval=_CPU_WINDOW_S, percpu=True)]
        cpu_total = round(sum(per_core) / len(per_core), 1) if per_core else None
    except Exception:  # noqa: BLE001
        per_core, cpu_total = [], None
    load_avg = None
    if hasattr(psutil, "getloadavg"):
        try:
            load_avg = [float(x) for x in psutil.getloadavg()]
        except (OSError, AttributeError):
            load_avg = None

    try:
        sw = psutil.swap_memory()
        gib = 1024 ** 3
        swap = {
            "used_gb": round(sw.used / gib, 2),
            "total_gb": round(sw.total / gib, 2),
            "percent": float(sw.percent),
        }
    except Exception:  # noqa: BLE001
        swap = {}

    sample = store.SystemSample(
        ts=time.time(),
        cpu_percent=cpu_total,
        per_core=per_core,
        load_avg=load_avg,
        ram=system_stats.ram_stats(),
        swap=swap,
        disk=system_stats.disk_stats(),
        disk_io=_io_counters(psutil.disk_io_counters),
        net_io=_io_counters(psutil.net_io_counters),
        gpus=system_stats.gpu_stats(),
        process_count=len(processes),
    )
    return sample, processes, ports


def _io_counters(fn) -> Dict[str, Any]:
    """Cumulative IO counters as a plain dict (deltas are derived at read
    time, so the stored value stays the raw counter)."""
    try:
        counters = fn()
    except Exception:  # noqa: BLE001
        return {}
    if counters is None:
        return {}
    try:
        return {k: v for k, v in counters._asdict().items() if isinstance(v, (int, float))}
    except AttributeError:
        return {}


def _write_tick(run_id: str) -> int:
    """Collect + persist one tick. Returns the process count written."""
    sample, processes, ports = _collect_tick()
    store.write_sample(run_id, sample, processes, ports)
    return len(processes)


# ------------------------------------------------------------- run control


async def start_run(
    *,
    interval_s: float = DEFAULT_INTERVAL_S,
    duration_s: Optional[float] = DEFAULT_DURATION_S,
    trigger: str = "manual",
    retention_days: int = 90,
) -> Dict[str, Any]:
    """Begin a capture. Raises ``RuntimeError`` if one is already running."""
    global _active, _task
    async with _lock:
        if _active is not None:
            raise RuntimeError("a capture is already running")

        interval = max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, float(interval_s)))
        duration: Optional[float] = None
        if duration_s is not None:
            duration = max(interval, min(MAX_DURATION_S, float(duration_s)))

        # Opportunistic retention — no timer to keep alive, the prune just
        # rides the action the user already took.
        try:
            await asyncio.to_thread(store.prune, retention_days)
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ diagnostics prune failed: %s", exc)

        run_id = await asyncio.to_thread(
            store.create_run,
            machine_id=_machine_id(),
            os_name=f"{platform.system()} {platform.release()}".strip(),
            hostname=socket.gethostname(),
            interval_s=interval,
            duration_s=duration,
            trigger=trigger,
            params={"retention_days": retention_days, "cpu_count": _cpu_count()},
        )
        _active = ActiveRun(
            run_id=run_id, started_at=time.time(), interval_s=interval,
            duration_s=duration, trigger=trigger,
        )
        # A previous run's stop signal must never abort the new one on its
        # first tick.
        _stop_requested.clear()
        _task = asyncio.create_task(_run_loop(_active))
        logger.info(
            "🔬 diagnostics capture started (run=%s interval=%.0fs duration=%s trigger=%s)",
            run_id, interval, f"{duration:.0f}s" if duration else "open-ended", trigger,
        )
        return _active.as_dict()


async def one_shot(*, retention_days: int = 90, trigger: str = "one-shot") -> Dict[str, Any]:
    """Take a single immediate sample as a complete run.

    Stored exactly like a timed run (one tick), so every reader — summary,
    verdict, drift, export — works on it unchanged."""
    async with _lock:
        if _active is not None:
            raise RuntimeError("a capture is already running")
        run_id = await asyncio.to_thread(
            store.create_run,
            machine_id=_machine_id(),
            os_name=f"{platform.system()} {platform.release()}".strip(),
            hostname=socket.gethostname(),
            interval_s=0.0,
            duration_s=0.0,
            trigger=trigger,
            params={"retention_days": retention_days, "cpu_count": _cpu_count()},
        )

    try:
        # Prime + settle so per-process CPU is real rather than the 0.0 a cold
        # read returns. (System CPU needs no priming — _collect_tick measures
        # it over its own window.)
        await asyncio.to_thread(attribution.prime_cpu_percent)
        await asyncio.sleep(1.0)
        await asyncio.to_thread(_write_tick, run_id)
        await asyncio.to_thread(store.finish_run, run_id, status="complete")
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ diagnostics one-shot failed: %s", exc)
        await asyncio.to_thread(store.finish_run, run_id, status="failed")
        raise

    from . import rules
    await asyncio.to_thread(rules.evaluate_and_save, run_id)
    logger.info("🔬 diagnostics one-shot captured (run=%s)", run_id)
    return {"run_id": run_id}


async def stop_run() -> Dict[str, Any]:
    """Stop the active capture gracefully, if any. Safe to call when idle.

    Signals the loop and lets it finish the tick it is in, rather than
    cancelling it. Cancelling mid-tick was wrong in a way that only showed up
    under load: the sampler does its work through ``asyncio.to_thread``, and
    cancelling the *await* does not stop the worker thread — so a half-written
    sample could still land in SQLite after the caller believed the run was
    over, and the finalize in the loop's ``finally`` could be skipped because
    its own awaits raise ``CancelledError`` too. A graceful stop makes both
    the last write and the finalize deterministic.

    ``cancel()`` remains only as a backstop for a loop that ignores the signal
    (a tick wedged in a syscall), so stopping can never hang forever."""
    global _task
    run = _active
    if run is None:
        return {"stopped": False}
    task = _task
    _stop_requested.set()
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_STOP_GRACE_S)
        except asyncio.TimeoutError:
            logger.warning("⚠️ diagnostics capture did not stop in %.0fs — cancelling",
                           _STOP_GRACE_S)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _task = None
    return {"stopped": True, "run_id": run.run_id}


async def _run_loop(run: ActiveRun) -> None:
    """The capture task: prime, then tick until the deadline or a stop signal.

    Every tick runs to completion before the loop can exit, so the store is
    never left mid-write (see :func:`stop_run`)."""
    global _active, _task
    status = "complete"
    try:
        await asyncio.to_thread(attribution.prime_cpu_percent)
        while True:
            if _stop_requested.is_set():
                status = "stopped"
                break
            tick_started = time.time()
            deadline = run.deadline
            if deadline is not None and tick_started >= deadline:
                break
            try:
                await asyncio.to_thread(_write_tick, run.run_id)
                run.samples_written += 1
                run.last_error = ""
            except Exception as exc:  # noqa: BLE001
                # A failed tick is recoverable — log, remember, keep sampling.
                # Losing one sample must not end a two-hour capture.
                run.last_error = type(exc).__name__
                logger.warning("⚠️ diagnostics tick failed (run=%s): %s", run.run_id, exc)

            # Subtract the work we just did so the cadence stays honest even
            # when a scan on a busy box takes seconds.
            elapsed = time.time() - tick_started
            sleep_for = max(0.0, run.interval_s - elapsed)
            if deadline is not None:
                sleep_for = min(sleep_for, max(0.0, deadline - time.time()))
                if time.time() + sleep_for >= deadline:
                    await _sleep_or_stop(sleep_for)
                    break
            # Interruptible: a stop lands within the signal, not a whole
            # interval later, without cancelling a tick that is mid-write.
            if await _sleep_or_stop(sleep_for):
                status = "stopped"
                break
    except asyncio.CancelledError:
        status = "stopped"
        raise
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        logger.warning("⚠️ diagnostics capture aborted (run=%s): %s", run.run_id, exc)
    finally:
        _active = None
        _task = None
        # Finalize synchronously on this thread rather than through
        # asyncio.to_thread: in the cancellation backstop path every *await*
        # here would re-raise CancelledError and silently skip the finalize,
        # leaving the run stuck at "running" forever. These are two short
        # SQLite writes, and the loop is already ending.
        try:
            _finalize(run.run_id, status)
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ diagnostics finalize failed (run=%s): %s", run.run_id, exc)
        logger.info(
            "🔬 diagnostics capture %s (run=%s, %d samples)",
            status, run.run_id, run.samples_written,
        )


def _finalize(run_id: str, status: str) -> None:
    """Close a run and judge it. Idempotent — ``finish_run`` is an UPDATE and
    the verdict write is INSERT OR REPLACE, so a double call is harmless."""
    store.finish_run(run_id, status=status)
    from . import rules
    rules.evaluate_and_save(run_id)


def _cpu_count() -> int:
    """Logical core count, stored on the run so a summary read on *another*
    machine still normalizes per-process CPU against the right hardware."""
    try:
        return int(psutil.cpu_count(logical=True) or 1)
    except Exception:  # noqa: BLE001
        return 1


def _machine_id() -> str:
    """This host's fleet id, falling back to the hostname off-registry."""
    try:
        from src.host_profile import resolve
        return resolve().id
    except Exception:  # noqa: BLE001
        return socket.gethostname()


# ------------------------------------------------------- scheduled snapshot


_scheduled_task: Optional[asyncio.Task] = None


async def start_scheduled_snapshots(interval_hours: float, retention_days: int = 90) -> None:
    """Start the opt-in periodic one-shot loop (default off).

    This is what makes multi-week trend lines exist without anyone remembering
    to press a button — and it still adds no process: it is one more asyncio
    task in the hub that sleeps between snapshots."""
    global _scheduled_task
    await stop_scheduled_snapshots()
    if interval_hours <= 0:
        return

    async def _loop() -> None:
        period = max(1.0, float(interval_hours)) * 3600.0
        while True:
            await asyncio.sleep(period)
            if _active is not None:
                continue  # a manual capture already covers this window
            try:
                await one_shot(retention_days=retention_days, trigger="scheduled")
            except Exception as exc:  # noqa: BLE001
                logger.warning("⚠️ scheduled diagnostics snapshot failed: %s", exc)

    _scheduled_task = asyncio.create_task(_loop())
    logger.info("🗓️ diagnostics scheduled snapshots every %.1fh", interval_hours)


async def stop_scheduled_snapshots() -> None:
    global _scheduled_task
    task = _scheduled_task
    _scheduled_task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


def scheduled_active() -> bool:
    return _scheduled_task is not None and not _scheduled_task.done()
