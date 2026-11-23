"""The one background-loop skeleton the always-on watchers share.

Health, resource, port and usage-digest each run a loop that reloads its config every tick, runs
one blocking tick in a worker thread, and sleeps the rest of the interval. Keeping that here means
"a failed tick never ends the loop" and "the cadence is the interval minus the tick's own time"
hold for every watcher by construction.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional
from src.concurrency.pools import BACKGROUND, run_on

logger = logging.getLogger(__name__)

Config = Dict[str, Any]


async def run_ticks(
    name: str,
    cfg: Config,
    *,
    load_cfg: Callable[[], Config],
    tick: Callable[[Config], Any],
    interval_of: Callable[[Config], float],
    floor_s: float,
    on_result: Optional[Callable[[Any, bool], Any]] = None,
    fail_label: str = "sample",
    exc_info: bool = False,
) -> None:
    """Tick forever, never raising out of a tick.

    Each pass reloads the config in a thread, runs ``tick(cfg)`` in a thread, then hands its result
    and whether it is the first success to ``on_result`` on the event loop. A failure is logged and
    the previous config's interval still paces the next pass. The sleep is the interval less the
    time the pass took, never below ``floor_s``.
    """
    first = True
    while True:
        started = time.monotonic()
        try:
            cfg = await run_on(BACKGROUND, load_cfg)
            result = await run_on(BACKGROUND, tick, cfg)
            if on_result is not None:
                on_result(result, first)
            first = False
        except Exception as exc:  # noqa: BLE001 — a failed tick must not end the loop
            logger.warning("⚠️ %s: %s failed: %s", name, fail_label, exc, exc_info=exc_info)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(floor_s, float(interval_of(cfg)) - elapsed))


async def run_periodic(
    name: str,
    *,
    load_cfg: Callable[[], Config],
    tick: Callable[[Config], Any],
    interval_of: Callable[[Config], float],
    floor_s: float,
    enabled_of: Callable[[Config], bool],
    boot_delay_of: Callable[[Config], float],
    disabled_in: str,
    on_loaded: Optional[Callable[[Config], Any]] = None,
    on_result: Optional[Callable[[Any, bool], Any]] = None,
    fail_label: str = "sample",
    exc_info: bool = False,
) -> None:
    """Load the config, return if the watcher is off, wait out the boot delay, then tick forever.

    ``on_loaded`` runs once on the first config, before the enabled check, so a watcher that serves
    its config to readers (even while disabled) can be handed it. ``disabled_in`` names the config
    file the off switch lives in, for the one info line a disabled watcher logs.
    """
    cfg = await run_on(BACKGROUND, load_cfg)
    if on_loaded is not None:
        on_loaded(cfg)
    if not enabled_of(cfg):
        logger.info("ℹ️ %s: disabled in %s", name, disabled_in)
        return
    await asyncio.sleep(float(boot_delay_of(cfg)))
    await run_ticks(name, cfg, load_cfg=load_cfg, tick=tick, interval_of=interval_of, floor_s=floor_s,
                    on_result=on_result, fail_label=fail_label, exc_info=exc_info)
