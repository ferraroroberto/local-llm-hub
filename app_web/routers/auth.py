"""Password login → bearer token swap.

The bearer token in ``webapp_config.json`` is never sent to the client
until a correct password is presented. Failed attempts and successful
logins both land in ``webapp/auth.log`` (separate from the main hub log
so a phone-side review is easy).
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request

from src.health.resource_alert import send_via_notify_script
from src.login_lockout import LoginLockout, LockoutAlert, retry_after_header
from src.webapp_config import WebappConfig

from ..middleware import LOOPBACK_HOSTS, is_pc_itself
from ._helpers import PROJECT_ROOT, client_ip, loaded_webapp_config, maybe_json

logger = logging.getLogger(__name__)
auth_logger = logging.getLogger("llmhub.auth")
_AUTH_LOG_PATH = PROJECT_ROOT / "webapp" / "auth.log"


def ensure_log_handler() -> None:
    """Attach the auth.log file handler exactly once. Idempotent."""
    if any(
        isinstance(h, logging.FileHandler)
        and Path(h.baseFilename).resolve() == _AUTH_LOG_PATH.resolve()
        for h in auth_logger.handlers
    ):
        return
    try:
        _AUTH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(_AUTH_LOG_PATH, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        auth_logger.addHandler(fh)
        auth_logger.setLevel(logging.INFO)
    except OSError as exc:
        logger.warning(f"⚠️  Could not open {_AUTH_LOG_PATH}: {exc}")


# The fleet's act-now Telegram chat; the transport resolves the chat id and
# bot token itself, so this repo holds neither.
ALERT_CATEGORY = "attention"
ALERT_TIMEOUT_S = 20.0
APP_NAME = "local-llm-hub"

# send(text, cfg) -> (delivered, error): the shape of ``send_via_notify_script``,
# injectable so tests never reach Telegram.
AlertSender = Callable[[str, Dict[str, Any]], Tuple[bool, Optional[str]]]


def build_lockout(
    send: AlertSender = send_via_notify_script, **kwargs: Any
) -> LoginLockout:
    """The one lock-out for the login route; ``kwargs`` tune the policy in tests."""

    def alert_owner(alert: LockoutAlert) -> None:
        held = f" (+{alert.suppressed} more held back)" if alert.suppressed else ""
        text = (
            f"🚨 {APP_NAME}: {alert.failures} wrong passwords in a row "
            f"from {alert.key}{held}"
        )

        def deliver() -> None:
            delivered, error = send(
                text, {"category": ALERT_CATEGORY, "timeout_s": ALERT_TIMEOUT_S}
            )
            if not delivered:
                logger.warning(f"⚠️  login alert not delivered: {error}")

        # The login request must not wait on Telegram.
        threading.Thread(target=deliver, daemon=True).start()

    return LoginLockout(on_alert=alert_owner, **kwargs)


def login_client(request: Request) -> str:
    """The caller's address, seen through a local tunnel or proxy.

    Behind a tunnel every public request reaches the hub over loopback, so the
    socket address names the proxy. The proxy's own header names the real
    client, and it is believed only when the socket is loopback: from any other
    peer the header is just text the caller chose.
    """
    host = client_ip(request)
    if host not in LOOPBACK_HOSTS:
        return host
    forwarded = request.headers.get("cf-connecting-ip") or (
        request.headers.get("x-forwarded-for", "").split(",")[0]
    )
    try:
        return str(ipaddress.ip_address(forwarded.strip()))
    except ValueError:
        return host


router = APIRouter()


@router.post("/api/login")
async def login(request: Request) -> Dict[str, Any]:
    who = login_client(request)
    lockout: LoginLockout = request.app.state.login_lockout
    exempt = is_pc_itself(client_ip(request), request.headers, request.method)
    wait = 0.0 if exempt else lockout.retry_after(who)
    if wait > 0:
        auth_logger.warning(f"🔒 Login from {who} refused: locked out for {wait:.0f} s")
        raise HTTPException(
            status_code=429,
            detail="too many attempts",
            headers=retry_after_header(wait),
        )
    if request.app.state.webapp_config is None:
        auth_logger.warning(
            f"⚠️  Login attempt from {who} but webapp config could not be loaded"
        )
    cfg: WebappConfig = loaded_webapp_config(request)
    if not cfg.auth_password:
        auth_logger.info(
            f"⚠️  Login attempt from {who} but no auth_password configured"
        )
        raise HTTPException(
            status_code=503, detail="password auth not configured"
        )
    if not cfg.auth_token:
        auth_logger.info(
            f"⚠️  Login attempt from {who} but no auth_token configured"
        )
        raise HTTPException(
            status_code=503, detail="bearer token not configured"
        )
    body = await maybe_json(request)
    presented = str(body.get("password") or "")
    # Bytes, not str: compare_digest raises TypeError on a non-ASCII str.
    if not presented or not hmac.compare_digest(
        presented.encode(), cfg.auth_password.encode()
    ):
        auth_logger.warning(
            f"🚨 Failed password attempt from {who} "
            f"(presented: {len(presented)} chars)"
        )
        if not exempt:
            lockout.record_failure(who)
        raise HTTPException(status_code=401, detail="bad password")
    lockout.reset(who)
    auth_logger.info(f"🔓 Password login from {who}")
    return {"token": cfg.auth_token}
