"""Playground tab API — model dropdown + send (proxies in-process to /v1/messages)."""

from __future__ import annotations

import base64
import logging
import mimetypes
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.host_profile import hub_port
from src.model_registry import enabled_models, resolve as resolve_model

logger = logging.getLogger(__name__)
router = APIRouter()

_IMAGE_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}


@router.get("/api/playground/models")
async def playground_models() -> Dict[str, Any]:
    """List every enabled model that can answer chat-style requests.

    Whisper rows are excluded — they're ASR-only and don't speak the
    /v1/messages shape.
    """
    rows: List[Dict[str, Any]] = []
    for m in enabled_models():
        if m.backend == "whisper":
            continue
        rows.append(
            {
                "id": m.id,
                "display_name": m.display_name,
                "backend": m.backend,
                "aliases": list(m.aliases or []),
                "image_capable": m.backend in ("claude", "gemini"),
            }
        )
    return {"models": rows}


@router.post("/api/playground/send")
async def playground_send(
    model: str = Form(...),
    prompt: str = Form(...),
    max_tokens: int = Form(512),
    system: Optional[str] = Form(None),
    attachment: Optional[UploadFile] = File(None),
) -> Dict[str, Any]:
    """Send a single-turn prompt through the hub. Returns text + usage.

    We proxy through the hub's *own* ``/v1/messages`` endpoint over
    loopback so the routing/observability path is identical to a real
    external call — the playground gets recorded in the live request
    ring like any other request, which is exactly what an operator wants
    when sanity-checking a new backend.
    """
    import httpx

    target = resolve_model(model)
    if target is None:
        raise HTTPException(status_code=400, detail=f"unknown model {model!r}")

    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    if attachment is not None and attachment.filename:
        raw = await attachment.read()
        suffix = (attachment.filename.rsplit(".", 1)[-1] or "").lower()
        b64 = base64.b64encode(raw).decode("ascii")
        if suffix in _IMAGE_MEDIA_TYPES:
            # Images route through the dedicated image block.
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": _IMAGE_MEDIA_TYPES[suffix],
                        "data": b64,
                    },
                }
            )
        else:
            # Everything else (PDF, JSON, CSV, code, …) is a document
            # block; the CLI attaches it as an @file the model can read.
            media = (
                mimetypes.guess_type(attachment.filename)[0]
                or attachment.content_type
                or "application/octet-stream"
            )
            content.append(
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": media, "data": b64},
                }
            )

    payload: Dict[str, Any] = {
        "model": target.display_name,
        "max_tokens": int(max_tokens),
        "messages": [{"role": "user", "content": content}],
    }
    if system:
        payload["system"] = system

    url = f"http://127.0.0.1:{hub_port()}/v1/messages"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}")
    body: Dict[str, Any] = {}
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not r.is_success:
        detail = (body.get("detail") if isinstance(body, dict) else None) or r.text or f"HTTP {r.status_code}"
        raise HTTPException(status_code=r.status_code, detail=str(detail)[:500])

    text = ""
    blocks = body.get("content") if isinstance(body, dict) else None
    if isinstance(blocks, list):
        text = "\n".join(
            (b.get("text") or "") for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return {
        "text": text,
        "stop_reason": body.get("stop_reason"),
        "usage": body.get("usage") or {},
    }
