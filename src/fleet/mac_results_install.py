"""Install the repo's ``assets/deploy/mac/bin/hub-results-pull.sh`` on the Mac runner host.

``python -m src.fleet.mac_results_install --host <machine id>`` compares the
SHA-256 and mode of ``~/bin/hub-results-pull.sh`` on the Mac with the repo's
copy and, when they differ, writes the repo's copy there with mode 755. The
results key itself is read-only (``restrict,command=``) and cannot write, so
this rides the hub user's own general passwordless SSH, the same channel the
power actions use (``remote_bootstrap``). Three states, never folded together:

  * ``unchanged``     - the Mac already holds the repo's bytes at mode 755.
  * ``installed``     - the Mac differed; it was written and read back equal.
  * ``not_confirmed`` - the Mac could not be read, the write failed, or the
    read-back differs. Nothing is claimed.

Nothing but a state word, a hash prefix and a mode ever reaches the output:
the Mac's stderr (which can carry an address or login) is dropped.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import remote_bootstrap
from .mac_results import HOST_ENV
from .ssh_exec import SshResult, run_ssh

logger = logging.getLogger(__name__)

UNCHANGED = "unchanged"
INSTALLED = "installed"
NOT_CONFIRMED = "not_confirmed"
WOULD_INSTALL = "would_install"

WANT_MODE = "755"
_PROBE_TIMEOUT_S = 20
_INSTALL_TIMEOUT_S = 30
_SCRIPT_REL = Path("assets") / "deploy" / "mac" / "bin" / "hub-results-pull.sh"

# Both commands run in the Mac user's login shell (zsh): POSIX constructs only.
# `shasum`/`stat -f` are macOS, `stat -c` the Linux fallback.
_REMOTE_FILE = '"$HOME/bin/hub-results-pull.sh"'
_PROBE_CMD = (
    f'f={_REMOTE_FILE}; if [ -f "$f" ]; then '
    'echo "$(shasum -a 256 "$f" | cut -d" " -f1) $(stat -f %Lp "$f" 2>/dev/null || stat -c %a "$f")"; '
    "else echo absent; fi"
)
_PROBE_RE = re.compile(r"^([0-9a-f]{64}) ([0-7]{3,4})$")

# (command, timeout, stdin text) -> result
Ssh = Callable[..., SshResult]


@dataclass(frozen=True)
class InstallOutcome:
    state: str
    detail: str


def repo_script_path() -> Path:
    return Path(__file__).resolve().parents[2] / _SCRIPT_REL


def _install_payload(content: bytes) -> str:
    """The script as one base64 line with no newline: stdin is text-mode, which
    would turn a newline into CRLF on this host."""
    return base64.b64encode(content).decode("ascii")


# Reads the payload from stdin (the command line truncates past ~8 KB), writes
# it beside the target, chmods it, then renames over the target, so the forced
# command is never half-written while a pull is mid-login. `base64 -d` is
# GNU/new macOS, `-D` older macOS; stdin is read once into a variable for that.
_INSTALL_CMD = (
    'b=$(cat); d="$HOME/bin"; f=' + _REMOTE_FILE + '; t="$d/.hub-results-pull.sh.new"; '
    'mkdir -p "$d" && { printf %s "$b" | base64 -d > "$t" 2>/dev/null || printf %s "$b" | base64 -D > "$t"; } '
    f'&& chmod {WANT_MODE} "$t" && mv -f "$t" "$f"'
)


def parse_probe(result: SshResult) -> Tuple[str, Optional[Tuple[str, str]]]:
    """``("ok", None)`` when the file is absent, ``("ok", (sha, mode))`` when
    present, ``("not_confirmed", None)`` for anything else - an ssh failure and
    an answer that is not one of those two shapes are the same non-fact."""
    if not result.ok:
        return NOT_CONFIRMED, None
    text = result.stdout.strip()
    if text == "absent":
        return "ok", None
    match = _PROBE_RE.match(text)
    if match is None:
        return NOT_CONFIRMED, None
    return "ok", (match.group(1), match.group(2))


def _is_current(found: Optional[Tuple[str, str]], want_sha: str) -> bool:
    return found is not None and found[0] == want_sha and found[1] in (WANT_MODE, "0" + WANT_MODE)


def install(ssh: Ssh, content: bytes, *, check_only: bool = False) -> InstallOutcome:
    want_sha = hashlib.sha256(content).hexdigest()
    short = want_sha[:12]
    state, found = parse_probe(ssh(_PROBE_CMD, _PROBE_TIMEOUT_S))
    if state != "ok":
        return InstallOutcome(NOT_CONFIRMED, "the Mac's copy could not be read")
    if _is_current(found, want_sha):
        return InstallOutcome(UNCHANGED, f"sha256 {short} mode {WANT_MODE}")
    if check_only:
        return InstallOutcome(WOULD_INSTALL, f"the Mac's copy is {'absent' if found is None else 'different'}; repo sha256 {short}")
    wrote = ssh(_INSTALL_CMD, _INSTALL_TIMEOUT_S, _install_payload(content))
    if not wrote.ok:
        return InstallOutcome(NOT_CONFIRMED, "the write did not complete")
    state, found = parse_probe(ssh(_PROBE_CMD, _PROBE_TIMEOUT_S))
    if state != "ok" or not _is_current(found, want_sha):
        return InstallOutcome(NOT_CONFIRMED, "the write finished but the read-back differs from the repo copy")
    return InstallOutcome(INSTALLED, f"sha256 {short} mode {WANT_MODE}")


def _ssh_for(host_id: str) -> Tuple[Optional[Ssh], str]:
    """The hub user's own general channel (no ``-i`` key) to ``host_id``."""
    owner, address, err = remote_bootstrap.resolve_dialable(host_id)
    if err is not None:
        return None, str(err["error"])

    def ssh(command: str, timeout: int, stdin_text: Optional[str] = None) -> SshResult:
        return run_ssh(f"{owner.ssh_user}@{address}", command, timeout=timeout, stdin_text=stdin_text)

    return ssh, ""


_ICONS = {UNCHANGED: "✅", INSTALLED: "✅", WOULD_INSTALL: "ℹ️", NOT_CONFIRMED: "⚠️"}
_EXIT = {UNCHANGED: 0, INSTALLED: 0, NOT_CONFIRMED: 1, WOULD_INSTALL: 3}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Install the repo's hub-results-pull.sh on the Mac runner host.")
    parser.add_argument("--host", default=None, help=f"machine id of the Mac runner host; default: ${HOST_ENV} from .env")
    parser.add_argument("--check", action="store_true", help="compare only: install nothing")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    except ImportError:
        pass
    host_id = (args.host or os.environ.get(HOST_ENV, "")).strip()
    if not host_id:
        print(f"❌ no host: pass --host or set {HOST_ENV} in .env")
        return 2
    script = repo_script_path()
    try:
        content = script.read_bytes()
    except OSError:
        print("❌ the repo's hub-results-pull.sh could not be read")
        return 2
    ssh, problem = _ssh_for(host_id)
    if ssh is None:
        print(f"❌ {problem}")
        return 2
    outcome = install(ssh, content, check_only=args.check)
    logger.info("hub-results-pull.sh on the Mac: %s", outcome.state)
    print(f"{_ICONS[outcome.state]} hub-results-pull.sh: {outcome.state} - {outcome.detail}")
    return _EXIT[outcome.state]


if __name__ == "__main__":
    sys.exit(main())
