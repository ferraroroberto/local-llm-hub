"""Pieces shared by the two chat routes: the SSE response wrapper and the
"this backend is not a chat model" rejection."""

from __future__ import annotations

from typing import Any, Optional

import anyio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from ..model_registry import Model

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def reject_non_chat_backend(model: Model, requested_name: str) -> Optional[HTTPException]:
    """Return the 400 to raise when a chat route is hit with an ASR/TTS backend.

    The whisper and tts backends don't serve chat completions; both chat
    routes (/v1/messages and /v1/chat/completions) reject them with the
    same backend-specific "POST to the right audio endpoint instead" 400.
    Returns ``None`` for any chat-capable backend so the caller can fall
    through to its normal handling.
    """
    if model.backend == "whisper":
        return HTTPException(
            status_code=400,
            detail=(
                f"{requested_name!r} is an ASR backend, not a chat model. "
                f"POST audio to http://127.0.0.1:{model.port}/v1/audio/transcriptions instead."
            ),
        )
    if model.backend == "tts":
        return HTTPException(
            status_code=400,
            detail=(
                f"{requested_name!r} is a TTS backend, not a chat model. "
                f"POST text to http://127.0.0.1:{model.port}/v1/audio/speech instead."
            ),
        )
    return None


def close_if_supported(iterator: Any) -> None:
    """Close a streaming iterator when its concrete type owns resources."""
    close = getattr(iterator, "close", None)
    if close is not None:
        close()


class ClosingStreamingResponse(StreamingResponse):
    """Ensure Starlette closes a synchronous stream after client disconnect."""

    def __init__(self, content: Any, **kwargs: Any) -> None:
        self._content_to_close = content
        super().__init__(content, **kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Starlette 1.0 cancels its threadpool adapter on http.disconnect
            # without closing the wrapped synchronous iterator. Shield this
            # cleanup so owned CLI processes and on-demand leases end before
            # the ASGI response returns.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(
                    close_if_supported,
                    self._content_to_close,
                )
