"""Bearer-token middleware for the /admin sub-app.

Loopback callers (PC itself) bypass the bearer token. Non-loopback
callers must present ``Authorization: Bearer <token>``, or
``?token=…`` on the URL (the latter is what bookmarked / shared
URLs use; the SPA strips it from ``window.location`` on first load).

WebSocket handshakes are not seen by this middleware. The /admin
sub-app intentionally does not expose websockets — SSE is enough for
the live-ops streams.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
from typing import Any, List

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Loopback addresses bypass the bearer-token gate so local probes keep
# working. Tunnel traffic arrives with a non-loopback client IP and must
# present the token. ``testclient`` is starlette's pseudo-host for its
# in-process TestClient — treating it as loopback keeps pytest happy
# without forcing every fixture to inject a fake token.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})

# Headers that a reverse proxy sets when forwarding a request. The
# presence of ANY of these means the loopback ``request.client.host``
# is the proxy's own address, not the real client — so we must enforce
# the bearer token even though the TCP source is 127.0.0.1.
#
# Covers:
#   * tailscale serve     → ``X-Forwarded-For``, ``X-Forwarded-Proto``
#   * cloudflared tunnel  → ``cf-ray``, ``cf-connecting-ip``
#   * generic nginx/caddy → ``X-Forwarded-For``
PROXY_HEADERS = (
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
    "cf-ray",
    "cf-connecting-ip",
)


def _is_proxied(headers) -> bool:
    for h in PROXY_HEADERS:
        if h in headers:
            return True
    return False

# Static + login + healthz/version are exempt: the login endpoint is
# how a phone-side user *gets* the token, and static assets must load
# before the login form can render. Paths here are the sub-app's view
# (the /admin mount prefix is stripped by starlette before we see them).
AUTH_EXEMPT_PREFIXES = ("/static/",)
AUTH_EXEMPT_EXACT = frozenset(
    {
        "",
        "/",            # SPA index — login overlay renders client-side
        "/api/login",
        "/api/healthz",
        "/api/version",
    }
)


def _client_in_allowlist(client_host: str, allowlist: List[str]) -> bool:
    try:
        ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    for entry in allowlist or []:
        try:
            if ip in ipaddress.ip_network(str(entry), strict=False):
                return True
        except ValueError:
            if client_host == str(entry):
                return True
    return False


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require Authorization: Bearer <token> on /admin endpoints (non-loopback only).

    Paths are the sub-app's view — starlette strips the /admin mount
    prefix before invoking us.
    """

    def __init__(self, app: Any, get_token):
        super().__init__(app)
        self._get_token = get_token

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # ``request.url.path`` here is the original *parent* path — e.g.
        # ``/admin/static/styles.css`` — because Starlette's ``BaseHTTPMiddleware``
        # runs BEFORE the parent's Mount strips the mount prefix. Strip
        # it manually so AUTH_EXEMPT_PREFIXES (``/static/``) matches.
        raw_path = request.url.path
        path = raw_path
        if path.startswith("/admin"):
            path = path[len("/admin"):] or "/"

        client_host = request.client.host if request.client else ""
        is_loopback = client_host in LOOPBACK_HOSTS and not _is_proxied(request.headers)

        token = (self._get_token() or "").strip()
        if not token:
            return await call_next(request)

        if is_loopback:
            return await call_next(request)
        cfg = getattr(request.app.state, "webapp_config", None)
        extra = getattr(cfg, "extra_allowlist", []) if cfg else []
        if _client_in_allowlist(client_host, extra):
            return await call_next(request)

        if path in AUTH_EXEMPT_EXACT or any(
            path.startswith(p) for p in AUTH_EXEMPT_PREFIXES
        ):
            return await call_next(request)

        presented = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            presented = auth_header[7:].strip()
        if not presented:
            presented = request.query_params.get("token", "").strip()

        if presented and hmac.compare_digest(presented, token):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "missing or invalid bearer token"},
            headers={"WWW-Authenticate": 'Bearer realm="local-llm-hub"'},
        )


# ----------------------------------------------------------------- parent

# Paths on the *parent* hub app (not the /admin sub-app) that bypass
# the bearer token even for non-loopback callers. The /admin SPA mounts
# under /admin so it has its own enforcement; / itself redirects there.
PARENT_AUTH_EXEMPT_PREFIXES = ("/admin/", "/admin")
PARENT_AUTH_EXEMPT_EXACT = frozenset(
    {
        "/",
        "/health",
        "/info",
        "/favicon.ico",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)


class ParentBearerTokenMiddleware(BaseHTTPMiddleware):
    """Same bearer enforcement as :class:`BearerTokenMiddleware`, but for
    the parent hub app — so a Cloudflare-exposed hub can't have its
    /v1/messages or /v1/chat/completions endpoints hit anonymously.

    The /admin sub-app is exempted here because it owns its own middleware.
    """

    def __init__(self, app: Any, get_token):
        super().__init__(app)
        self._get_token = get_token

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        client_host = request.client.host if request.client else ""
        is_loopback = client_host in LOOPBACK_HOSTS and not _is_proxied(request.headers)

        if path in PARENT_AUTH_EXEMPT_EXACT or any(
            path.startswith(p) for p in PARENT_AUTH_EXEMPT_PREFIXES
        ):
            return await call_next(request)

        token = (self._get_token() or "").strip()
        if not token:
            return await call_next(request)

        if is_loopback:
            return await call_next(request)
        cfg = getattr(request.app.state, "webapp_config", None)
        extra = getattr(cfg, "extra_allowlist", []) if cfg else []
        if _client_in_allowlist(client_host, extra):
            return await call_next(request)

        presented = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            presented = auth_header[7:].strip()
        if not presented:
            presented = request.query_params.get("token", "").strip()

        if presented and hmac.compare_digest(presented, token):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "missing or invalid bearer token"},
            headers={"WWW-Authenticate": 'Bearer realm="local-llm-hub"'},
        )
