"""Unit tests for src/wake_on_lan.py (issue #356)."""

from __future__ import annotations

import socket

import pytest

from src import wake_on_lan


def test_magic_packet_is_102_bytes():
    packet = wake_on_lan.magic_packet("aa:bb:cc:dd:ee:ff")
    assert len(packet) == 102


def test_magic_packet_starts_with_six_ff_bytes():
    packet = wake_on_lan.magic_packet("aa:bb:cc:dd:ee:ff")
    assert packet[:6] == b"\xff" * 6


def test_magic_packet_body_is_mac_repeated_16_times():
    packet = wake_on_lan.magic_packet("aa:bb:cc:dd:ee:ff")
    mac_bytes = bytes.fromhex("aabbccddeeff")
    assert packet[6:] == mac_bytes * 16
    assert len(packet[6:]) == 96


def test_colon_and_hyphen_forms_produce_identical_payload():
    colon = wake_on_lan.magic_packet("aa:bb:cc:dd:ee:ff")
    hyphen = wake_on_lan.magic_packet("AA-BB-CC-DD-EE-FF")
    assert colon == hyphen


def test_case_insensitive():
    lower = wake_on_lan.magic_packet("aa:bb:cc:dd:ee:ff")
    upper = wake_on_lan.magic_packet("AA:BB:CC:DD:EE:FF")
    assert lower == upper


@pytest.mark.parametrize(
    "mac",
    [
        "",
        "aa:bb:cc:dd:ee",             # too short
        "aa:bb:cc:dd:ee:ff:11",       # too long
        "zz:bb:cc:dd:ee:ff",          # non-hex
        "aabbccddeeff",               # no separators
        "aa:bb:cc:dd:ee_ff",          # bad separator
        "aa-bb:cc-dd:ee-ff",          # mixed separators
        "not a mac address at all",   # garbage
    ],
)
def test_malformed_mac_raises_typed_error(mac):
    with pytest.raises(wake_on_lan.WakeOnLanError):
        wake_on_lan.magic_packet(mac)


class _FakeSocket:
    def __init__(self, *args, **kwargs):
        self.sockopts = []
        self.sent = []
        self.closed = False

    def setsockopt(self, level, optname, value):
        self.sockopts.append((level, optname, value))

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def test_send_wake_sets_broadcast_and_sends_packet(monkeypatch):
    created = {}

    def fake_socket_factory(family, type_):
        assert family == socket.AF_INET
        assert type_ == socket.SOCK_DGRAM
        sock = _FakeSocket()
        created["sock"] = sock
        return sock

    monkeypatch.setattr(wake_on_lan.socket, "socket", fake_socket_factory)

    wake_on_lan.send_wake("aa:bb:cc:dd:ee:ff", broadcast="192.168.1.255", port=9)

    sock = created["sock"]
    assert (socket.SOL_SOCKET, socket.SO_BROADCAST, 1) in sock.sockopts
    assert len(sock.sent) == 1
    data, addr = sock.sent[0]
    assert data == wake_on_lan.magic_packet("aa:bb:cc:dd:ee:ff")
    assert addr == ("192.168.1.255", 9)


def test_send_wake_wraps_socket_error(monkeypatch):
    class _RaisingSocket(_FakeSocket):
        def sendto(self, data, addr):
            raise OSError("network unreachable")

    monkeypatch.setattr(
        wake_on_lan.socket, "socket", lambda family, type_: _RaisingSocket()
    )

    with pytest.raises(wake_on_lan.WakeOnLanError):
        wake_on_lan.send_wake("aa:bb:cc:dd:ee:ff")
