"""Shared pieces for the e2e flow tests.

Each e2e test walks one user-visible flow as several phases. A phase is a
function that takes a ``page`` and drives one scenario; every phase gets its own
page, and ``fresh_page`` clears the cookies, storage and permissions it
leaves behind, so the routes, init scripts, listeners, viewport and stored state
of one scenario never leak into the next.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

from playwright.sync_api import Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}

# Counts the pairs of elements that share a horizontal band: 0 means they stack.
SIDE_BY_SIDE = """sel => {
  const r = [...document.querySelectorAll(sel)].map(e => e.getBoundingClientRect())
    .filter(b => b.width > 0 && b.height > 0);
  let pairs = 0;
  for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) {
    const overlapY = Math.min(r[i].bottom, r[j].bottom) - Math.max(r[i].top, r[j].top);
    if (overlapY > 1) pairs++;
  }
  return {count: r.length, sideBySide: pairs};
}"""

# The bound every phase's actions and navigations get, so a stuck click names
# itself instead of stacking toward Playwright's opaque 30 s default.
PHASE_TIMEOUT_MS = 15000

# PWA icons are optional placeholders that can 404 while the manifest settles.
# WebKit logs "due to access control checks" when a tab switch aborts an
# in-flight streaming fetch (the hub's request and log streams); Chromium logs
# nothing for an abort, and a genuinely blocked request reads differently there.
_TOLERATED_CONSOLE_NOISE = ("icon-180", "icon-512", "due to access control checks")


# One browser context per (browser, context args), kept for the whole session.
# A closed context drops its connections to the hub, and every closed connection
# parks a loopback ephemeral port in TIME_WAIT; a drained pool fails the next
# page load with ``net::ERR_ADDRESS_IN_USE`` (see ``_port_pool.py``). Reusing
# the context keeps its connections and HTTP cache alive across phases.
_ContextKey = Tuple[int, Tuple[Tuple[str, str], ...]]
_SHARED_CONTEXTS: Dict[_ContextKey, BrowserContext] = {}


def _shared_context(browser: Browser, context_args: Dict[str, Any]) -> BrowserContext:
    key: _ContextKey = (id(browser), tuple(sorted((k, repr(v)) for k, v in context_args.items())))
    context = _SHARED_CONTEXTS.get(key)
    if context is None:
        context = browser.new_context(**context_args)
        _SHARED_CONTEXTS[key] = context
    return context


def close_shared_contexts() -> None:
    """Close every context ``fresh_page`` opened; called once at session end."""
    for context in _SHARED_CONTEXTS.values():
        try:
            context.close()
        except Exception as exc:  # noqa: BLE001 - teardown must reach every context
            logger.warning("⚠️  closing a shared browser context failed: %s", exc)
    _SHARED_CONTEXTS.clear()


def _close_and_reset(context: BrowserContext, page: Page) -> None:
    """Close the phase's page and return the shared context to a first-visit state.

    Everything a phase can leave behind that the next one could read: the
    page's own storage, and the context's cookies and permissions. Its routes,
    init scripts and listeners die with the page; ``unroute_all`` is skipped
    because it blocks on a handler still holding a request. A step that fails
    is logged and skipped, never raised, so one broken page cannot mask the
    phase's own result.
    """
    steps = (
        lambda: page.evaluate(
            "() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"
        ),
        page.close,
        context.clear_cookies,
        context.clear_permissions,
    )
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("⚠️  shared context reset step failed: %s", exc)


@contextmanager
def fresh_page(
    browser: Browser,
    *,
    console_errors: bool = False,
    page_errors: bool = False,
    **context_args: Any,
) -> Iterator[Page]:
    """Yield a new page in the session's shared context for ``context_args``.

    The page is closed and the context reset afterwards. ``console_errors``
    fails the phase on any ``console.error`` line and ``page_errors`` on any
    uncaught page exception. A phase that stubs a failing response leaves them
    off: the browser logs the non-2xx itself.
    """
    context = _shared_context(browser, context_args)
    page = context.new_page()
    page.set_default_timeout(PHASE_TIMEOUT_MS)
    page.set_default_navigation_timeout(PHASE_TIMEOUT_MS)
    errors: List[str] = []

    def _is_noise(text: str) -> bool:
        return any(n in text for n in _TOLERATED_CONSOLE_NOISE)

    def _on_console(msg) -> None:
        if msg.type == "error" and not _is_noise(msg.text):
            errors.append(msg.text)

    def _on_page_error(exc) -> None:
        if not _is_noise(str(exc)):
            errors.append(str(exc))

    if console_errors:
        page.on("console", _on_console)
    if page_errors:
        page.on("pageerror", _on_page_error)
    try:
        yield page
    finally:
        _close_and_reset(context, page)
    if errors:
        raise AssertionError("console errors: " + " | ".join(errors))


SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def snapshot(page: Page, name: str, browser_name: str) -> None:
    """Save a full-page screenshot to ``tests/e2e/snapshots/`` (git-ignored).

    The sparklines re-render every 2.5 s from live RAM/GPU readings, so they are
    masked: the picture stays useful for a human to look at without noise.
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOT_DIR / f"{name}-{browser_name}.png"
    spark = page.locator("#hubSparklines")
    page.screenshot(path=str(out), full_page=True, mask=[spark] if spark.count() else [])
    assert out.exists() and out.stat().st_size > 0, f"empty screenshot at {out}"


def fulfil_json(route, body: Any, status: int = 200) -> None:
    """Answer an intercepted request with ``body`` as JSON."""
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
