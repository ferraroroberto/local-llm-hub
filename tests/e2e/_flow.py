"""Shared pieces for the e2e flow tests.

Each e2e test walks one user-visible flow as several phases. A phase is a
function that takes a ``page`` and drives one scenario; every phase runs in its
own browser context so the routes, init scripts, listeners, viewport and stored
state of one scenario never leak into the next.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, List

from playwright.sync_api import Browser, Page

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
_TOLERATED_CONSOLE_NOISE = ("icon-180", "icon-512")


@contextmanager
def fresh_page(
    browser: Browser,
    *,
    console_errors: bool = False,
    page_errors: bool = False,
    **context_args: Any,
) -> Iterator[Page]:
    """Yield a page in a new browser context; close the context afterwards.

    ``console_errors`` fails the phase on any ``console.error`` line and
    ``page_errors`` on any uncaught page exception. A phase that stubs a failing
    response leaves them off: the browser logs the non-2xx itself.
    """
    context = browser.new_context(**context_args)
    page = context.new_page()
    page.set_default_timeout(PHASE_TIMEOUT_MS)
    page.set_default_navigation_timeout(PHASE_TIMEOUT_MS)
    errors: List[str] = []

    def _on_console(msg) -> None:
        if msg.type == "error" and not any(n in msg.text for n in _TOLERATED_CONSOLE_NOISE):
            errors.append(msg.text)

    if console_errors:
        page.on("console", _on_console)
    if page_errors:
        page.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        yield page
    finally:
        context.close()
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
