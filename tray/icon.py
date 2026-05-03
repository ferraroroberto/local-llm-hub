"""Tray-icon glyph rendered at runtime with PIL — no image file in the repo."""

from __future__ import annotations

from PIL import Image, ImageDraw

# Status palette: green = hub healthy, amber = starting, red/grey = stopped.
COLOR_RUNNING = (70, 180, 120)
COLOR_STARTING = (230, 170, 50)
COLOR_STOPPED = (140, 140, 140)


def make_icon_image(color: tuple[int, int, int] = COLOR_RUNNING) -> Image.Image:
    """Return a 64×64 RGBA hub glyph: a centre node with three radiating arms."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2
    # Three radiating arms (top, lower-left, lower-right) — evokes a hub.
    arm_w = 6
    draw.line((cx, cy, cx, 8), fill=color, width=arm_w)
    draw.line((cx, cy, 12, 52), fill=color, width=arm_w)
    draw.line((cx, cy, 52, 52), fill=color, width=arm_w)
    # Endpoint dots.
    for ex, ey in ((cx, 8), (12, 52), (52, 52)):
        draw.ellipse((ex - 6, ey - 6, ex + 6, ey + 6), fill=color)
    # Centre node, slightly brighter so the hub reads at 16×16 in the tray.
    draw.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), fill=color)
    draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(255, 255, 255, 220))
    return img
