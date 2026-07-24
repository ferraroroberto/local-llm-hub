"""Fleet machine console (#309) — the data layer behind the Machines tab.

Turns the host inventory (``host_profile.all_hosts()``) into a per-machine
view for the admin SPA, with the **same** CPU/RAM/GPU/disk/uptime snapshot on
every machine and a liveness signal that reflects *is the box powered on* —
not whether the hub happens to run there:

  * the **current machine** snapshots ``system_stats`` locally
    (:func:`self_snapshot`, also served at ``/admin/api/machines/self``);
  * a **peer** is liveness-probed by a hub-independent TCP connect
    (``remote_stats.is_reachable``) and, when up, gives the same snapshot over
    the hub user's own SSH (``remote_stats.collect``);
  * a **dormant** node (a powered-down box, if any is flagged) is shown
    but never live-probed.

Power actions (reboot/shutdown) live in ``remote_bootstrap`` and run over the
hub user's own general SSH (#311); this module only computes *which* actions
each machine may offer. The active hub host never offers reboot/shutdown —
the destructive action the whole design excludes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from src import remote_stats, system_stats
from src.host_profile import HostProfile, all_hosts, resolve

logger = logging.getLogger(__name__)


async def self_snapshot() -> Dict[str, Any]:
    """Detailed snapshot of *this* machine for its Machines-tab card.

    Same shape ``remote_stats.collect`` produces for a peer, so every card
    renders through identical code. ``gpu_stats`` shells out to nvidia-smi so
    it runs off the event loop (same pattern as ``/api/hub/stats``)."""
    gpus = await asyncio.to_thread(system_stats.gpu_stats)
    return {
        "cpu": system_stats.cpu_stats(),
        "ram": system_stats.ram_stats(),
        "gpus": gpus,
        "disk": system_stats.disk_stats(),
        "uptime_seconds": system_stats.uptime_seconds(),
    }


def _actions_for(
    host: HostProfile, *, is_host: bool, reachable: Optional[bool] = None
) -> Dict[str, bool]:
    """Which actions the SPA may offer for this machine.

    The active hub host offers none (reboot/shutdown are the excluded
    destructive actions; SSH/RDP to self is pointless — and waking the box
    the hub is already running on is meaningless). A peer with SSH (address
    + ssh_user) gets reboot/shutdown + an SSH terminal; RDP is offered
    wherever an ``rdp`` target is configured; wake (#356) is offered
    wherever a ``mac`` is configured, independent of SSH/reachability —
    Wake-on-LAN's whole point is reaching a box that's down.

    ``reachable`` mirrors the card's own liveness signal (the Online/Offline
    dot) — ``True`` only for a peer whose TCP probe just succeeded. Anything
    else (dormant, an unreachable peer, no probe path, or a probe error —
    all ``None``/``False``) disables every SSH/RDP-dependent action (#388):
    they can only fail against a box nothing is listening on. Wake survives
    unreachability by design, since that's the one action meant to reach a
    box that's down."""
    if is_host:
        return {"reboot": False, "shutdown": False, "rdp": False, "ssh_terminal": False, "wake": False}
    wake = bool(host.mac)
    if reachable is not True:
        return {"reboot": False, "shutdown": False, "ssh_terminal": False, "rdp": False, "wake": wake}
    return {
        "reboot": host.can_ssh,
        "shutdown": host.can_ssh,
        "ssh_terminal": host.can_ssh,
        "rdp": bool(host.rdp),
        "wake": wake,
    }


def _card_base(
    host: HostProfile, *, is_host: bool, reachable: Optional[bool] = None
) -> Dict[str, Any]:
    """The static (non-probe) fields of a machine card."""
    return {
        "id": host.id,
        "display_name": host.display_name or host.id,
        "role": host.role or "",
        "icon": host.icon or "server",
        "platform": host.platform,
        "is_host": is_host,
        "dormant": host.dormant,
        "has_tailscale": bool(host.tailscale),
        # Managed-only machines (openclaw, gaming) declare no `enabled`
        # models and never run this hub at all (config/models.yaml's own
        # comment) — the SPA uses this to decide whether a peer card should
        # point the user at that machine's own /admin for Diagnostics.
        "runs_hub": bool(host.enabled),
        # Wired-NIC MAC from config (#356's Wake-on-LAN field) — surfaced
        # here too (#397) so it's visible on the card itself, not just used
        # internally to gate the Wake button. Static, so it shows even on a
        # down/dormant/unreachable peer, unlike the live-probed `network`
        # block below (which needs a successful SSH round-trip).
        "mac": host.mac,
        "actions": _actions_for(host, is_host=is_host, reachable=reachable),
    }


async def _probe_machine(host: HostProfile, active_id: str) -> Dict[str, Any]:
    """Build one machine's full card: static fields + live probe."""
    is_host = host.id == active_id

    # This machine — full local snapshot, always "up". No live network probe
    # here (#397 scopes connection type/AP/signal to *peers* — you already
    # know how the box you're looking at is connected).
    if is_host:
        card = _card_base(host, is_host=True)
        stats = await self_snapshot()
        card.update(
            state="self", reachable=None,
            uptime_seconds=stats.get("uptime_seconds"), stats=stats, detail="",
            network=None, flaky=None,
        )
        return card

    # Dormant node — shown but never live-probed (it is powered down).
    if host.dormant:
        card = _card_base(host, is_host=False, reachable=None)
        card.update(
            state="dormant", reachable=None, uptime_seconds=None, stats=None,
            detail="Dormant — Remote Desktop only",
            network=None, flaky=None,
        )
        return card

    # A peer with a network address — liveness by TCP (is the box on?),
    # then the same CPU/RAM/GPU/disk/uptime snapshot over general SSH. The
    # probe reports WHICH address answered (LAN, or the tailscale name when
    # only the tailnet does — #396) so a silent wired failure surfaces as a
    # "via tailnet" badge on the card instead of being masked by the fallback.
    if host.address:
        located = await remote_stats.located_address(host)
        reachable = located is not None
        card = _card_base(host, is_host=False, reachable=reachable)
        stats = await remote_stats.collect(host) if reachable else None
        card.update(
            state="up" if reachable else "down",
            reachable=reachable,
            via_tailscale=bool(
                reachable and host.tailscale
                and located == host.tailscale and host.tailscale != host.address
            ),
            uptime_seconds=stats.get("uptime_seconds") if stats else None,
            stats=stats,
            # Live connection type/MAC/AP/signal (#397) — riding the same SSH
            # round-trip as the rest of `stats`, so no extra probe cost. None
            # when the platform has no probe for it, or the box is down.
            network=stats.get("network") if stats else None,
            # Connection-health proxy (#397): did the liveness probe that
            # produced this very card need its #333 warm-up retry? Only
            # meaningful for a peer we're reporting reachable right now.
            flaky=remote_stats.connection_flaky(host) if reachable else None,
            detail="" if reachable else "Offline",
        )
        return card

    # No probe path (no address, not dormant) — show it, but honestly unknown.
    card = _card_base(host, is_host=False, reachable=None)
    card.update(
        state="down", reachable=None, uptime_seconds=None, stats=None,
        detail="No probe path configured",
        network=None, flaky=None,
    )
    return card


async def machines_status() -> Dict[str, Any]:
    """The whole fleet for the Machines tab — every host as a probed card.

    Probes run concurrently; each is individually soft-failing so one dead
    peer never blocks the rest. Returns ``{active_id, machines: [...]}``."""
    active_id = resolve().id
    hosts = all_hosts()
    cards = await asyncio.gather(
        *(_probe_machine(h, active_id) for h in hosts),
        return_exceptions=True,
    )
    machines: List[Dict[str, Any]] = []
    for host, card in zip(hosts, cards):
        if isinstance(card, Exception):
            logger.warning("⚠️ machine probe failed for %s: %s", host.id, card)
            machines.append({
                **_card_base(host, is_host=host.id == active_id),
                "state": "down", "reachable": None, "uptime_seconds": None,
                "stats": None, "detail": "Probe error",
                "network": None, "flaky": None,
            })
        else:
            machines.append(card)
    return {"active_id": active_id, "machines": machines}


# --------------------------------------------------------------------- RDP


def rdp_file(host_id: str) -> Optional[tuple[str, str]]:
    """Generate a minimal ``.rdp`` launcher for a machine, or ``None``.

    Returns ``(filename, content)``. The file is built from the host's
    configured ``rdp`` target ({address, user}) rather than depending on any
    out-of-repo launcher file, so the console is self-contained. The SPA
    serves it as a download; the viewer's own RDP client opens it (device-
    agnostic — works whether the admin PWA is on the phone, Mac, or PC)."""
    from src.host_profile import get_host

    host = get_host(host_id)
    if host is None or not host.rdp:
        return None
    address = host.rdp.get("address")
    if not address:
        return None
    user = host.rdp.get("user", "")
    lines = [
        "screen mode id:i:2",
        "use multimon:i:0",
        "desktopwidth:i:1920",
        "desktopheight:i:1080",
        "session bpp:i:32",
        "compression:i:1",
        "keyboardhook:i:2",
        "audiocapturemode:i:0",
        "redirectclipboard:i:1",
        "displayconnectionbar:i:1",
        "autoreconnection enabled:i:1",
        "authentication level:i:2",
        "prompt for credentials:i:1",
        f"full address:s:{address}",
    ]
    if user:
        lines.append(f"username:s:{user}")
    content = "\r\n".join(lines) + "\r\n"
    return f"{host_id}.rdp", content
