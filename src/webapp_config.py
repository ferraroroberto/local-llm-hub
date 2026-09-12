"""Webapp-specific configuration loader for the /admin sub-app.

Stored separately from ``config/models.yaml`` because these settings are
authored from the web UI ("Save settings" button) and persist across runs.
The tray also reads this file so all surfaces share one source of truth.

Holds:
  * auth token (bearer) and optional password gate
  * WebAuthn relying-party identity for the passkey gate
  * Cloudflare tunnel hostname (read-only mirror of the hostname pulled
    from ``webapp/cloudflared.yml``; cached here so the tray can copy a
    URL without re-parsing yaml on every menu refresh)
  * tailnet allowlist (extra IPs/CIDRs beyond loopback that bypass
    bearer-token enforcement)
  * CORS allowed origins (extra browser origins beyond loopback that may
    read a hub response from page JavaScript)
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "webapp_config.json"
SAMPLE_CONFIG_PATH = PROJECT_ROOT / "config" / "webapp_config.sample.json"


@dataclass
class WebappConfig:
    """User-authored, persisted webapp settings."""

    # Bearer token enforced when the request did NOT come from a
    # loopback IP. Empty string disables enforcement entirely.
    auth_token: str = ""
    # Optional password gate that hands the bearer token back to the
    # browser when the user types it correctly. Lets a fresh device
    # bootstrap without copy-pasting a tokenised URL.
    auth_password: str = ""
    # WebAuthn relying-party identity. ``rp_id`` is the bare public
    # hostname (e.g. ``llm.example.com``); ``origin`` is the full
    # https origin the phone connects to. Empty disables the passkey gate.
    webauthn_rp_id: str = ""
    webauthn_rp_name: str = "Local LLM Hub"
    webauthn_origin: str = ""
    # Extra IPs / CIDRs allowed to bypass the bearer-token gate on top
    # of loopback. Empty by default — keep auth strict.
    extra_allowlist: list = field(default_factory=list)
    # Extra browser origins allowed to call the hub from page JavaScript,
    # on top of loopback (which is always allowed — see src/cors_policy.py).
    # Exact origins only, e.g. "https://llm.example.com"; a "*" entry is
    # dropped with a warning rather than honoured. Read once at startup,
    # so a change here needs a hub restart — unlike ``auth_token``.
    cors_allow_origins: list = field(default_factory=list)


class WebappConfigError(Exception):
    """The config file exists but could not be read or parsed.

    Deliberately not folded into ``WebappConfig()``: defaults are what an
    *unset* field means (``auth_token=""`` is "enforcement off"), so a caller
    handed defaults for a broken file acts on settings nobody chose (#585).
    Each caller decides what "unknown" means for it.
    """


def load_webapp_config(path: Optional[Path] = None) -> WebappConfig:
    """Load the webapp config; defaults only when the file is absent.

    Raises :class:`WebappConfigError` when the file exists but is unreadable,
    not JSON, not a JSON object, or carries a field of an unusable type.
    """
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not target.exists():
        logger.info(
            f"📂 webapp_config not found at {target}, using defaults "
            f"(file will be created when settings change)"
        )
        return WebappConfig()

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError(f"top level is {type(raw).__name__}, not an object")
        return WebappConfig(
            auth_token=str(raw.get("auth_token", "")),
            auth_password=str(raw.get("auth_password", "")),
            webauthn_rp_id=str(raw.get("webauthn_rp_id", "")),
            webauthn_rp_name=str(raw.get("webauthn_rp_name", "Local LLM Hub")),
            webauthn_origin=str(raw.get("webauthn_origin", "")),
            extra_allowlist=list(raw.get("extra_allowlist") or []),
            cors_allow_origins=list(raw.get("cors_allow_origins") or []),
        )
    except (OSError, ValueError, TypeError) as exc:
        # ValueError covers json.JSONDecodeError. The message names the file
        # and the parser's position, never the file's contents.
        raise WebappConfigError(f"could not read {target}: {exc}") from exc


def save_webapp_config(cfg: WebappConfig, path: Optional[Path] = None) -> Path:
    """Atomically write the config back to disk."""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "auth_token": cfg.auth_token,
        "auth_password": cfg.auth_password,
        "webauthn_rp_id": cfg.webauthn_rp_id,
        "webauthn_rp_name": cfg.webauthn_rp_name,
        "webauthn_origin": cfg.webauthn_origin,
        "extra_allowlist": cfg.extra_allowlist,
        "cors_allow_origins": cfg.cors_allow_origins,
    }

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    logger.info(f"💾 Saved webapp_config to {target}")
    return target


def update_webapp_config(**fields) -> WebappConfig:
    """Read, patch, save — convenience for the API endpoint.

    Lets :class:`WebappConfigError` propagate: saving a patch over defaults
    would replace an unreadable file and discard every setting it held.
    """
    current = load_webapp_config()
    patched = replace(current, **fields)
    save_webapp_config(patched)
    return patched


def ensure_auth_token(cfg: Optional[WebappConfig] = None) -> WebappConfig:
    """Mint a random bearer token if the config has none. Returns the
    (possibly updated) config. Called by the tray on boot so the user
    never has an unprotected non-loopback hub by accident.

    Lets :class:`WebappConfigError` propagate: minting a token over an
    unreadable file would overwrite it, losing its password, allowlist and
    CORS origins (#585).
    """
    cfg = cfg if cfg is not None else load_webapp_config()
    if cfg.auth_token:
        return cfg
    cfg.auth_token = secrets.token_urlsafe(32)
    save_webapp_config(cfg)
    logger.info("🔐 Generated a fresh bearer token for the admin webapp")
    return cfg


def append_auth_token(url: str, token: Optional[str]) -> str:
    """Return ``url`` with ``?token=<token>`` appended when ``token`` is set."""
    if not token:
        return url
    parsed = urlparse(url)
    existing = parsed.query
    extra = urlencode({"token": token})
    new_query = f"{existing}&{extra}" if existing else extra
    return urlunparse(parsed._replace(query=new_query))
