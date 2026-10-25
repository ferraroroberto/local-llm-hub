"""Wake-on-LAN magic packet builder + sender (#356).

Builds the standard 102-byte magic packet (6 bytes of ``0xFF`` followed by
the target MAC repeated 16 times) and fires it as a UDP broadcast. Stdlib
``socket`` only — no subprocess, no external dependencies. Fire-and-forget:
there is no confirmation a sleeping machine actually woke, only that the
packet was handed to the OS for broadcast.
"""

from __future__ import annotations

import logging
import re
import socket

logger = logging.getLogger(__name__)

_MAC_RE = re.compile(
    r"^[0-9A-Fa-f]{2}([:-])"
    r"(?:[0-9A-Fa-f]{2}\1){4}"
    r"[0-9A-Fa-f]{2}$"
)


class WakeOnLanError(ValueError):
    """Raised for a malformed MAC address or a failed packet send."""


def _mac_to_bytes(mac: str) -> bytes:
    """Validate ``mac`` and return its raw 6 bytes.

    Accepts ``aa:bb:cc:dd:ee:ff`` or ``AA-BB-CC-DD-EE-FF`` (case-insensitive,
    ``:`` or ``-`` separators, not mixed within one address).
    """
    if not _MAC_RE.match(mac):
        raise WakeOnLanError(f"malformed MAC address: {mac!r}")
    hex_digits = mac.replace(":", "").replace("-", "")
    try:
        return bytes.fromhex(hex_digits)
    except ValueError as exc:
        raise WakeOnLanError(f"malformed MAC address: {mac!r}") from exc


def magic_packet(mac: str) -> bytes:
    """Build the 102-byte Wake-on-LAN magic packet for ``mac``.

    Payload is ``0xFF`` * 6 followed by the 6-byte MAC repeated 16 times.
    Raises :class:`WakeOnLanError` for a malformed ``mac``.
    """
    mac_bytes = _mac_to_bytes(mac)
    return b"\xff" * 6 + mac_bytes * 16


def send_wake(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    """Send a Wake-on-LAN magic packet for ``mac`` via UDP broadcast.

    Fire-and-forget — no acknowledgement is possible for WoL, so this only
    reports whether the packet was handed off successfully. Any socket
    failure is wrapped in :class:`WakeOnLanError`.
    """
    packet = magic_packet(mac)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, (broadcast, port))
    except OSError as exc:
        raise WakeOnLanError(f"failed to send wake packet for {mac!r}: {exc}") from exc
    logger.info("sent wake-on-lan packet to %s via %s:%d", mac, broadcast, port)
