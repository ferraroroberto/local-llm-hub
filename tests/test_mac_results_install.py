"""The Mac script installer's states, with SSH faked: unchanged / installed /
not_confirmed stay distinct, and nothing the Mac says leaks into the report."""

from __future__ import annotations

import base64
import hashlib
from typing import List, Optional

from src.fleet import mac_results_install as mi
from src.fleet.ssh_exec import SshResult

CONTENT = b"#!/bin/bash\necho hi\n"
SHA = hashlib.sha256(CONTENT).hexdigest()


def _ok(out: str = "") -> SshResult:
    return SshResult(returncode=0, stdout=out, stderr="")


class FakeMac:
    """Holds one file (sha, mode) or none; a write replaces it with what was sent."""

    def __init__(self, held: Optional[tuple] = None) -> None:
        self.held = held
        self.commands: List[str] = []
        self.write_result: Optional[SshResult] = None
        self.write_lands = True
        self.probe_result: Optional[SshResult] = None

    def __call__(self, command: str, timeout: int, stdin_text: Optional[str] = None) -> SshResult:
        self.commands.append(command)
        if command == mi._PROBE_CMD:
            if self.probe_result is not None:
                return self.probe_result
            return _ok("absent\n" if self.held is None else f"{self.held[0]} {self.held[1]}\n")
        if self.write_result is not None:
            return self.write_result
        if self.write_lands:
            sent = base64.b64decode(stdin_text)
            self.held = (hashlib.sha256(sent).hexdigest(), "755")
        return _ok()

    @property
    def writes(self) -> int:
        return sum(1 for c in self.commands if c != mi._PROBE_CMD)


def test_unchanged_when_hash_and_mode_match_and_nothing_is_written():
    mac = FakeMac((SHA, "755"))
    out = mi.install(mac, CONTENT)
    assert out.state == mi.UNCHANGED
    assert mac.writes == 0


def test_installed_when_absent_and_read_back_equal():
    mac = FakeMac(None)
    out = mi.install(mac, CONTENT)
    assert out.state == mi.INSTALLED
    assert mac.held == (SHA, "755")


def test_installed_when_content_differs():
    mac = FakeMac(("0" * 64, "755"))
    assert mi.install(mac, CONTENT).state == mi.INSTALLED


def test_installed_when_only_the_mode_is_wrong():
    mac = FakeMac((SHA, "644"))
    assert mi.install(mac, CONTENT).state == mi.INSTALLED
    assert mac.writes == 1


def test_not_confirmed_when_the_probe_cannot_run():
    mac = FakeMac(None)
    mac.probe_result = SshResult(returncode=255, stdout="", stderr="ssh: connect to host 10.0.0.9 refused")
    out = mi.install(mac, CONTENT)
    assert out.state == mi.NOT_CONFIRMED
    assert mac.writes == 0
    assert "10.0.0.9" not in out.detail


def test_not_confirmed_when_the_probe_answer_is_not_a_known_shape():
    mac = FakeMac(None)
    mac.probe_result = _ok("sh: shasum: command not found\n")
    assert mi.install(mac, CONTENT).state == mi.NOT_CONFIRMED


def test_not_confirmed_when_the_write_fails():
    mac = FakeMac(None)
    mac.write_result = SshResult(returncode=1, stdout="", stderr="login user@host: denied")
    out = mi.install(mac, CONTENT)
    assert out.state == mi.NOT_CONFIRMED
    assert "user@host" not in out.detail


def test_not_confirmed_when_the_write_reports_ok_but_the_readback_differs():
    mac = FakeMac(None)
    mac.write_lands = False
    assert mi.install(mac, CONTENT).state == mi.NOT_CONFIRMED


def test_check_only_never_writes():
    mac = FakeMac(None)
    out = mi.install(mac, CONTENT, check_only=True)
    assert out.state == mi.WOULD_INSTALL
    assert mac.writes == 0
    assert mi.install(FakeMac((SHA, "755")), CONTENT, check_only=True).state == mi.UNCHANGED


def test_content_rides_stdin_not_the_command_line():
    mac = FakeMac(None)
    mi.install(mac, CONTENT)
    assert "chmod 755" in mac.commands[1]
    assert base64.b64encode(CONTENT).decode() not in mac.commands[1]
    assert "\n" not in mi._install_payload(CONTENT)


def test_repo_script_exists():
    assert mi.repo_script_path().is_file()
