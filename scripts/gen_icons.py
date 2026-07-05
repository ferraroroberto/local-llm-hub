"""Generate PWA/Stream-Deck icons from the shared fleet icon-brand generator.

Thin caller onto ``project-scaffolding``'s ``brand_gen.render_set()`` — the
master art is local-llm-hub's vendored Lucide ``hub.svg`` (app-launcher#65: a
coherent icon family across the fleet). Replaces the runtime placeholder
glyph in ``app_web/icons.py`` (issue #209) with real, committed assets.

The tray's live state-tinted icon (``tray/icon.py``) renders the same
``hub.svg`` shape independently via resvg at runtime, so it isn't part of
this generator's output — see that module's docstring.

Writes into ``app_web/static/``: ``icon-180.png``, ``icon-192.png``,
``icon-512.png``, ``icon-512-maskable.png``, ``favicon.ico``. Into
``assets/stream-deck/``: ``local-llm-hub-144.png``.

Usage:
    python scripts/gen_icons.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SCAFFOLDING_SCRIPTS = Path(r"E:\automation\project-scaffolding\scripts")
sys.path.insert(0, str(SCAFFOLDING_SCRIPTS))

from brand_gen import render_set  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "app_web" / "static"


def main() -> None:
    render_set(
        master=Path(r"E:\automation\project-scaffolding\brand\hub.svg"),
        out_dir=STATIC_DIR,
        tray_out_dir=None,
        stream_deck_out_dir=PROJECT_ROOT / "assets" / "stream-deck",
        project_slug="local-llm-hub",
        emit_tray=False,
    )
    print(f"wrote icons to {STATIC_DIR}")


if __name__ == "__main__":
    main()
