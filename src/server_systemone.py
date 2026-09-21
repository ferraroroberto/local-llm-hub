"""TypeSafe "System One" passthrough route (``POST /v1/systemone``, #611).

TypeSafe's Jev is a decision model, not a text generator: a request carries a
``state`` plus a map of typed ``questions`` (Noul / Choice / Score) and the
answer is ``{model, answers, usage}`` with per-option probabilities and a
confidence. None of that survives a squeeze through ``/v1/messages`` or
``/v1/chat/completions``, so it gets its own route.

This is the hub's **first third-party internet egress** — every other route
reaches a local CLI, a llama-server backend or a sibling hub on the LAN. The
route is therefore deliberately thin and strictly opt-in:

* The JSON body is forwarded **unchanged** and the vendor's status + body come
  back **unchanged** (401 / 422 / 429 / 529 included). The hub adds the API key
  and observability; it never reinterprets the payload.
* **No retries.** The vendor SDKs already back off on 429/529; a second retry
  layer here would multiply load against a rate limit. ``Retry-After`` is
  passed through so the caller's policy has what it needs.
* Nothing falls back to this route and it is not listed in ``GET /v1/models``.

Hub-originated failures are distinguishable from vendor ones by status *and*
by a ``hub_error`` field in the body (the vendor never sends one):

* key unset          → 503 ``hub_error=typesafe_not_configured`` (no egress)
* vendor unreachable → 502 ``hub_error=typesafe_unreachable``
* vendor too slow    → 504 ``hub_error=typesafe_timeout``

The API key is read from ``TYPESAFE_API_KEY`` (``.env``) on every call, and is
never logged, echoed, or placed in an error body or ring entry.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .http_client import get_async_client
from .observability import (
    hash_prompts_enabled,
    record_genai_metrics,
    set_genai_payload,
    set_genai_request_attrs,
    set_genai_response_attrs,
)
from .server_common import client_id_from, current_otel_span, stash_trace_id_on_ctx

logger = logging.getLogger(__name__)

router = APIRouter()

ROUTE = "/v1/systemone"
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
BACKEND = "typesafe"
API_KEY_ENV = "TYPESAFE_API_KEY"
# Outbound ceiling for one evaluation. Generous against a vendor-side p99 we
# have no data on yet; a stalled call surfaces as its own 504, not a hang.
TIMEOUT_S = 60.0
# Vendor error text kept in the ring's error_detail. A 422 can echo the
# caller's input back, so it is dropped entirely when OTEL_HASH_PROMPTS is on.
_MAX_VENDOR_SNIPPET = 200

_UPSTREAM_LABELS = {
    401: "typesafe upstream auth rejected (401)",
    422: "typesafe request validation failed (422)",
    429: "typesafe rate limited (429)",
    529: "typesafe overloaded (529)",
}


def _hub_error(status: int, kind: str, message: str) -> JSONResponse:
    """A hub-originated failure — ``hub_error`` marks it as not the vendor's."""
    return JSONResponse(status_code=status, content={"detail": message, "hub_error": kind})


def _question_count(doc: Any) -> Optional[int]:
    questions = doc.get("questions") if isinstance(doc, dict) else None
    return len(questions) if isinstance(questions, dict) else None


def _upstream_error_detail(status: int, text: str) -> str:
    label = _UPSTREAM_LABELS.get(status, f"typesafe upstream error ({status})")
    snippet = (text or "").strip()
    if snippet and not hash_prompts_enabled():
        label += ": " + snippet[:_MAX_VENDOR_SNIPPET]
    return label


@router.post(ROUTE)
async def systemone(request: Request) -> Response:
    """Forward one System One evaluation to TypeSafe and relay its answer."""
    ctx = getattr(request.state, "obs_ctx", None)
    body = await request.body()
    try:
        doc = json.loads(body) if body else None
    except ValueError:
        doc = None  # forwarded anyway — the vendor's 422 is the authoritative answer
    model = str(doc.get("model") or "") if isinstance(doc, dict) else ""
    n_questions = _question_count(doc)

    if ctx is not None:
        ctx.backend = BACKEND
        if n_questions is not None:
            ctx.detail = f"{n_questions} question{'' if n_questions == 1 else 's'}"
    logger.info("%s model=%s questions=%s", ROUTE, model or "?", n_questions)

    client_id = client_id_from(request)
    span = current_otel_span()
    set_genai_request_attrs(
        span, model=model, backend=BACKEND, operation="systemone", client_id=client_id,
    )
    stash_trace_id_on_ctx(ctx, span)
    start_ns = time.monotonic_ns()

    def _metrics(error_type: str = "", in_tok: int = 0, out_tok: int = 0) -> None:
        record_genai_metrics(
            model=model, backend=BACKEND, route=ROUTE, client_id=client_id,
            duration_ms=(time.monotonic_ns() - start_ns) / 1e6,
            input_tokens=in_tok, output_tokens=out_tok, error_type=error_type,
        )

    def _fail(status: int, kind: str, message: str) -> JSONResponse:
        if ctx is not None:
            ctx.error_detail = f"{kind}: {message}"
        _metrics(error_type=kind)
        logger.warning("⚠️ %s %s: %s", ROUTE, kind, message)
        return _hub_error(status, kind, message)

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        return _fail(
            503, "typesafe_not_configured",
            f"TypeSafe API key not configured — set {API_KEY_ENV} in the hub's .env",
        )

    try:
        upstream = await get_async_client().post(
            TYPESAFE_URL,
            content=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=TIMEOUT_S,
        )
    except httpx.TimeoutException:
        return _fail(
            504, "typesafe_timeout",
            f"TypeSafe did not answer within {TIMEOUT_S:.0f}s (request may still be in flight upstream)",
        )
    except httpx.HTTPError as e:
        # Type name only: an httpx message can carry the request URL, and a
        # stable string keeps ring entries groupable.
        return _fail(502, "typesafe_unreachable", f"TypeSafe unreachable ({type(e).__name__})")

    status = upstream.status_code
    in_tok = out_tok = 0
    if status < 400:
        try:
            answer = upstream.json()
        except ValueError:
            answer = None
        if isinstance(answer, dict):
            usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
            in_tok = int(usage.get("input_tokens") or 0)
            out_tok = int(usage.get("output_tokens") or 0)
            if ctx is not None:
                ctx.served_model = str(answer.get("model") or "")
                ctx.in_tok, ctx.out_tok = in_tok, out_tok
        set_genai_response_attrs(span, input_tokens=in_tok, output_tokens=out_tok)
        set_genai_payload(span, body.decode("utf-8", errors="replace"), upstream.text)
        _metrics(in_tok=in_tok, out_tok=out_tok)
        logger.info("<- %s %d in=%d out=%d", ROUTE, status, in_tok, out_tok)
    else:
        if ctx is not None:
            ctx.error_detail = _upstream_error_detail(status, upstream.text)
        _metrics(error_type=f"upstream_{status}")
        logger.warning("⚠️ %s upstream status=%d", ROUTE, status)

    headers: Dict[str, str] = {}
    if "retry-after" in upstream.headers:
        headers["Retry-After"] = upstream.headers["retry-after"]
    return Response(
        content=upstream.content,
        status_code=status,
        media_type=upstream.headers.get("content-type", "application/json"),
        headers=headers,
    )
