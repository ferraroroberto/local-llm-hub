"""Password login → bearer token swap.

The bearer token in ``webapp_config.json`` is never sent to the client
until a correct password is presented. Failed attempts and successful
logins both land in ``auth.log`` under the runtime-data root (separate from the
main hub log so a phone-side review is easy).
"""

from __future__ import annotations

import ipaddress
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request

from src import password
from src.fsio.runtime_files import file_handler, make_dirs
from src.health.resource_alert import DEFAULT_SCRIPT, send_via_notify_script
from src.login_alert import build_login_alert
from src.login_lockout import LoginLockout, retry_after_header
from src.runtime_data import runtime_data_dir
from src.webapp_config import WebappConfig

from ..middleware import LOOPBACK_HOSTS, is_pc_itself
from ._helpers import client_ip, loaded_webapp_config, maybe_json

logger = logging.getLogger(__name__)
auth_logger = logging.getLogger("llmhub.auth")
_AUTH_LOG_PATH = runtime_data_dir("local-llm-hub") / "auth.log"


def ensure_log_handler() -> None:
    """Attach the auth.log file handler exactly once. Idempotent."""
    if any(
        isinstance(h, logging.FileHandler)
        and Path(h.baseFilename).resolve() == _AUTH_LOG_PATH.resolve()
        for h in auth_logger.handlers
    ):
        return
    try:
        make_dirs(_AUTH_LOG_PATH.parent)
        fh = file_handler(_AUTH_LOG_PATH)
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


class _NotifyTransport:
    """``send_text`` over the fleet transport, raising when it reports no delivery."""

    def __init__(self, send: AlertSender) -> None:
        self._send = send

    def send_text(self, text: str) -> None:
        delivered, error = self._send(
            text, {"category": ALERT_CATEGORY, "timeout_s": ALERT_TIMEOUT_S}
        )
        if not delivered:
            raise RuntimeError(error or "notify transport reported no delivery")


def build_lockout(
    send: AlertSender = send_via_notify_script,
    transport_script: Path = DEFAULT_SCRIPT,
    **kwargs: Any,
) -> LoginLockout:
    """The one lock-out for the login route; ``kwargs`` tune the policy in tests.

    A machine without the fleet transport has no alert channel, which the
    vendored alert reports as ``unconfigured``.
    """

    def notifier() -> Optional[_NotifyTransport]:
        return _NotifyTransport(send) if transport_script.is_file() else None

    return LoginLockout(on_alert=build_login_alert(APP_NAME, notifier), **kwargs)


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
    if not password.is_configured(legacy=cfg.auth_password):
        auth_logger.info(
            f"⚠️  Login attempt from {who} but no password configured"
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
    if not password.check_password(presented, legacy=cfg.auth_password):
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
