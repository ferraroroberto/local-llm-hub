"""Resolve the base URL of a model's *owning* host when it's not this one.

A model row can declare ``host: <host-id>`` (see ``Model.host`` in
``src/model_registry.py``) to mark which host spawns/manages its process.
When the active host is a *different* one, requests for that model must be
forwarded to the owning host's own hub instead of dispatched locally. This
module is the single place that turns "which host owns this model" into
"what URL do I forward to" — every dispatch path (chat, audio, admin model
control) shares it rather than re-deriving the address.
"""

from __future__ import annotations

import os
from typing import Optional

from .host_profile import get_host, hub_port
from .host_profile import resolve as resolve_host
from .model_registry import Model


def remote_base_url(model: Model) -> Optional[str]:
    """Owning host's hub base URL (e.g. ``http://192.168.0.14:8000``, no
    trailing slash, no ``/v1``) when ``model`` is remote relative to the
    active host — ``None`` when it's local (no ``host`` set, or it matches
    the active host) or when the owning host has no ``address`` configured.
    """
    model_host = getattr(model, "host", None)
    if not model_host:
        return None
    active = resolve_host()
    if model_host == active.id:
        return None
    owner = get_host(model_host)
    if owner is None or not owner.address:
        return None
    return f"http://{owner.address}:{hub_port()}"


def remote_auth_token(owning_host_id: str) -> Optional[str]:
    """Optional bearer token to send when calling a remote host's hub.

    Env-var-driven, one var per target host id (``LOCAL_LLM_HUB_TOKEN_<ID>``,
    id uppercased with ``-`` -> ``_``) — unset by default. The primary
    cross-host auth story is the receiving hub's own ``extra_allowlist``
    (trusted LAN IP, no token exchange needed); this is a defensive extra
    for setups that don't rely on IP allowlisting.
    """
    env_name = "LOCAL_LLM_HUB_TOKEN_" + owning_host_id.upper().replace("-", "_")
    return os.environ.get(env_name) or None
