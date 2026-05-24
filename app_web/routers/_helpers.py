"""Cross-router helpers — no router imports another router; shared utility
lives here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import Request

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def maybe_json(request: Request) -> Dict[str, Any]:
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"
