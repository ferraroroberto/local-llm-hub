"""Tray-icon glyph rendered at runtime via resvg — no static image file in
the repo, so the icon can be tinted by hub health state.

Renders Lucide's ``share-2`` glyph (24x24 viewBox) — the fleet's canonical
"hub-with-3-arms" identity for this project (app-launcher#65's shared icon
family; the vendored master lives at ``project-scaffolding/brand/hub.svg``).
The path data below is embedded here rather than read from that repo at
runtime: this module is called on every tray health-state change, and a live
cross-repo file read on that hot path is more fragile than a one-shot
dev-time generator import. Re-copy the path data from
``project-scaffolding/brand/hub.svg`` if it ever changes there.
"""

from __future__ import annotations

import io

import resvg_py
from PIL import Image

# Status palette: green = hub healthy, amber = starting, grey = stopped.
COLOR_RUNNING = (70, 180, 120)
COLOR_STARTING = (230, 170, 50)
COLOR_STOPPED = (140, 140, 140)

# Lucide `share-2` glyph paths, 24x24 viewBox — vendored verbatim from
# project-scaffolding/brand/hub.svg.
_GLYPH_PATHS = """
    <circle cx="18" cy="5" r="3" />
    <circle cx="6" cy="12" r="3" />
    <circle cx="18" cy="19" r="3" />
    <line x1="8.59" x2="15.42" y1="13.51" y2="17.49" />
    <line x1="15.41" x2="8.59" y1="6.51" y2="10.49" />
"""

_SIZE = 64
_PAD_RATIO = 0.1


def make_icon_image(color: tuple[int, int, int] = COLOR_RUNNING) -> Image.Image:
    """Return a 64x64 RGBA hub glyph (Lucide share-2), tinted by ``color``."""
    glyph_size = _SIZE * (1 - 2 * _PAD_RATIO)
    offset = _SIZE * _PAD_RATIO
    scale = glyph_size / 24
    r, g, b = color
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{_SIZE}" height="{_SIZE}" viewBox="0 0 {_SIZE} {_SIZE}">
  <g transform="translate({offset},{offset}) scale({scale})"
     fill="none" stroke="rgb({r},{g},{b})" stroke-width="2.6"
     stroke-linecap="round" stroke-linejoin="round">
    {_GLYPH_PATHS}
  </g>
</svg>"""
    png_bytes = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=_SIZE, height=_SIZE))
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")
