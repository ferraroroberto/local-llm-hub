"""Local LLM Hub admin webapp — FastAPI sub-app mounted at /admin.

Replaces the Streamlit control panel. Three tabs: Hub, Models, Playground.
Lives inside the hub's own FastAPI process; no second port, no second
Python process.

Auth model (mirrors app-launcher):
  * Loopback callers (PC itself) bypass the bearer token.
  * Non-loopback callers must present ``Authorization: Bearer <token>``
    (or ``?token=…`` on the initial URL). Token is hashed-compare.
  * Optional WebAuthn passkey gate on top, for the iOS-PWA case where the
    bearer token has leaked into a browser history but a passkey assertion
    is still required to mint a fresh session.

Cloudflare tunnel termination is supported via ``webapp/cloudflared.yml``;
the tray surfaces the configured public hostname as a "Copy Cloudflare URL"
menu item with ``?token=…`` appended.
"""

from __future__ import annotations

from .server import create_app

__all__ = ["create_app"]
