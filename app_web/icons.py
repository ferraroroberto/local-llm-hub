"""Render placeholder PNG icons (180/512 px) once at create_app() time.

A real icon family is tracked in issue #6 (pointer to app-launcher#65). Until
that lands, this generates a minimal hub glyph at boot so the manifest's
icon URLs resolve. Idempotent — skips work if the files already exist.

Uses PIL (already required by the tray for the system-tray glyph).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_BG = (15, 17, 21, 255)       # var(--bg)
_ACCENT = (217, 119, 87, 255)  # var(--accent)
_ACCENT_2 = (240, 167, 133, 255)


def _draw_glyph(size: int) -> "object":
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), _BG)
    draw = ImageDraw.Draw(img)

    # Rounded corners
    radius = int(size * 0.22)
    mask = Image.new("L", (size, size), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle((0, 0, size, size), radius=radius, fill=255)

    # Dish triangle (filled, semi-transparent accent)
    cx = size // 2
    top = int(size * 0.34)
    base_l = int(size * 0.22)
    base_r = size - base_l
    base_y = int(size * 0.66)
    tri_fill = (_ACCENT[0], _ACCENT[1], _ACCENT[2], 80)
    draw.polygon([(base_l, base_y), (cx, top), (base_r, base_y)], fill=tri_fill, outline=_ACCENT, width=max(2, size // 40))

    # Vertical pole
    pole_w = max(2, size // 40)
    draw.line([(cx, top), (cx, int(size * 0.22))], fill=_ACCENT, width=pole_w)
    # Probe
    probe = max(3, size // 22)
    draw.ellipse(
        (cx - probe, int(size * 0.18) - probe, cx + probe, int(size * 0.18) + probe),
        fill=_ACCENT,
    )

    # Base
    draw.line([(int(size * 0.32), int(size * 0.78)), (int(size * 0.68), int(size * 0.78))], fill=_ACCENT, width=pole_w)
    draw.line([(cx, base_y), (cx, int(size * 0.78))], fill=_ACCENT, width=pole_w)

    # Signal arc 1
    arc_w = max(2, size // 50)
    draw.arc(
        (int(size * 0.55), int(size * 0.20), int(size * 0.85), int(size * 0.50)),
        start=-50, end=50, fill=_ACCENT_2, width=arc_w,
    )
    # Signal arc 2 — outer, lighter
    draw.arc(
        (int(size * 0.50), int(size * 0.12), int(size * 0.94), int(size * 0.56)),
        start=-50, end=50, fill=(_ACCENT_2[0], _ACCENT_2[1], _ACCENT_2[2], 140), width=arc_w,
    )

    # Apply rounded mask
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def ensure_placeholders(static_dir: Path) -> None:
    """Render icon-180.png + icon-512.png if not already present."""
    targets = [(180, static_dir / "icon-180.png"), (512, static_dir / "icon-512.png")]
    if all(p.exists() for _, p in targets):
        return
    try:
        from PIL import Image  # noqa: F401 — gate the import here
    except ImportError:
        logger.info("ℹ️ Pillow not available — skipping placeholder icon generation")
        return
    static_dir.mkdir(parents=True, exist_ok=True)
    for size, path in targets:
        if path.exists():
            continue
        try:
            glyph = _draw_glyph(size)
            glyph.save(path, format="PNG")
            logger.info(f"🎨 Wrote placeholder icon {path.name} ({size}px)")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"⚠️ Could not write {path}: {exc}")
