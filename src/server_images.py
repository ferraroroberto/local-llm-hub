"""Image generation + edit routes (OpenAI ``/v1/images/*`` shape).

Split out of ``server.py`` so the routing/chat core stays readable. The only
image backend the hub can reach is Google's Imagen, exposed as an agentic tool
inside ``agy`` (there is no Nano Banana picker model — issue #114), so both
routes guard on a gemini row flagged ``image_gen`` and 400 everything else.

The routes are collected on a module-level :class:`fastapi.APIRouter` and
mounted onto the parent hub app by ``server.py`` via ``include_router``.
"""

from __future__ import annotations

import base64
import logging
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .gemini_cli import GeminiCLIError, call_gemini_image
from .observability import record_genai_metrics, set_genai_request_attrs
from .server_common import (
    client_id_from,
    current_otel_span,
    resolve_model_or_400,
    stash_trace_id_on_ctx,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class ImagesGenerationRequest(BaseModel):
    model: str
    prompt: str
    n: int = 1
    response_format: str = "b64_json"


@router.post("/v1/images/generations")
def images_generations(req: ImagesGenerationRequest, request: Request) -> JSONResponse:
    """Generate an image and return it OpenAI-shape (``data[].b64_json``).

    Routes exclusively to gemini rows flagged ``image_gen``; every other
    backend is text-only and 400s. The call lands in the observability ring
    like other hub traffic.
    """
    model = resolve_model_or_400(req.model)
    if not (model.backend == "gemini" and model.image_gen):
        raise HTTPException(
            status_code=400,
            detail=(
                f"model {req.model!r} ({model.display_name}) is not an "
                "image-generation model. Use 'gemini_image' instead."
            ),
        )
    if req.n != 1:
        raise HTTPException(
            status_code=400,
            detail="only n=1 is supported for image generation",
        )
    if req.response_format != "b64_json":
        raise HTTPException(
            status_code=400,
            detail="only response_format='b64_json' is supported",
        )

    ctx = getattr(request.state, "obs_ctx", None)
    if ctx is not None:
        ctx.backend = model.backend
    logger.info("/v1/images/generations model=%s", req.model)

    client_id = client_id_from(request)
    span = current_otel_span()
    set_genai_request_attrs(
        span,
        model=req.model,
        backend=model.backend,
        operation="image_generation",
        client_id=client_id,
    )
    stash_trace_id_on_ctx(ctx, span)

    start_ns = time.monotonic_ns()
    try:
        out = call_gemini_image(req.prompt)
    except GeminiCLIError as e:
        record_genai_metrics(
            model=req.model, backend=model.backend,
            route="/v1/images/generations", client_id=client_id,
            duration_ms=(time.monotonic_ns() - start_ns) / 1e6,
            error_type="gemini_cli_error",
        )
        raise HTTPException(status_code=502, detail=str(e))

    b64 = base64.b64encode(out["image_bytes"]).decode("ascii")
    record_genai_metrics(
        model=req.model, backend=model.backend,
        route="/v1/images/generations", client_id=client_id,
        duration_ms=(time.monotonic_ns() - start_ns) / 1e6,
    )
    logger.info(
        "<- image bytes=%d media=%s backend=%s",
        len(out["image_bytes"]), out["media_type"], model.backend,
    )
    return JSONResponse({
        "created": int(time.time()),
        "data": [{"b64_json": b64}],
    })


@router.post("/v1/images/edits")
async def images_edits(
    request: Request,
    image: UploadFile = File(...),
    prompt: str = Form(...),
    model: str = Form("gemini_image"),
    response_format: str = Form("b64_json"),
    n: int = Form(1),
) -> JSONResponse:
    """Edit an uploaded image (OpenAI ``/v1/images/edits`` shape, multipart).

    Routes to `agy`'s image path with the upload as a reference; the model
    edits it and the hub returns the result OpenAI-shape (``data[].b64_json``).
    Editing is agentic and procedural (`agy` often scripts the edit), so it is
    slower and best-effort. Same backend guard as generations.
    """
    resolved = resolve_model_or_400(model)
    if not (resolved.backend == "gemini" and resolved.image_gen):
        raise HTTPException(
            status_code=400,
            detail=(
                f"model {model!r} ({resolved.display_name}) is not an "
                "image-generation model. Use 'gemini_image' instead."
            ),
        )
    if n != 1:
        raise HTTPException(status_code=400, detail="only n=1 is supported")
    if response_format != "b64_json":
        raise HTTPException(
            status_code=400,
            detail="only response_format='b64_json' is supported",
        )

    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty image upload")

    ctx = getattr(request.state, "obs_ctx", None)
    if ctx is not None:
        ctx.model = model
        ctx.backend = resolved.backend
    logger.info("/v1/images/edits model=%s bytes=%d", model, len(raw))

    client_id = client_id_from(request)
    span = current_otel_span()
    set_genai_request_attrs(
        span, model=model, backend=resolved.backend,
        operation="image_edit", client_id=client_id,
    )
    stash_trace_id_on_ctx(ctx, span)

    suffix = Path(image.filename or "input.png").suffix or ".png"
    start_ns = time.monotonic_ns()
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            tf.write(raw)
            tmp_path = Path(tf.name)
        try:
            out = call_gemini_image(prompt, reference_image=tmp_path)
        except GeminiCLIError as e:
            record_genai_metrics(
                model=model, backend=resolved.backend,
                route="/v1/images/edits", client_id=client_id,
                duration_ms=(time.monotonic_ns() - start_ns) / 1e6,
                error_type="gemini_cli_error",
            )
            raise HTTPException(status_code=502, detail=str(e))
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    b64 = base64.b64encode(out["image_bytes"]).decode("ascii")
    record_genai_metrics(
        model=model, backend=resolved.backend,
        route="/v1/images/edits", client_id=client_id,
        duration_ms=(time.monotonic_ns() - start_ns) / 1e6,
    )
    logger.info(
        "<- edited image bytes=%d media=%s", len(out["image_bytes"]),
        out["media_type"],
    )
    return JSONResponse({
        "created": int(time.time()),
        "data": [{"b64_json": b64}],
    })
