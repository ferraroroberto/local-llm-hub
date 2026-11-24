r"""One shell-neutral tray restart that fails unless a new process serves ``HEAD``.

Run it from any shell, identically (Git Bash, PowerShell, cmd)::

    .venv\Scripts\python.exe -m src.restart_check restart --port <webapp port>

It reads the served ``/api/version`` identity (``--version-path`` names another path, e.g.
``/admin/api/version``, for an app that serves it elsewhere), runs this checkout's ``tray.bat --restart``
by absolute path, then polls until a *different* process answers with a ``git_sha`` that
matches ``HEAD``. It exits ``0`` only then; ``1`` when the restart ran but did not replace
the old process (the bat failed or hung, or the old build still serves); ``2`` when the
outcome cannot be established (no ``tray.bat``, an answer with nothing to tell old from
new). Never ``0`` without a proof.

Why not type ``tray.bat --restart``: on a host that sets ``NoDefaultCurrentDirectoryInExePath``
the bare name is found by no shell (Git Bash exits 127, PowerShell prints an error and leaves
a stale ``$LASTEXITCODE``, ``cmd /c`` exits 1), and Git Bash ``cmd.exe /c "tray.bat --restart"``
never enters the script yet exits 0. The spellings that work by hand are ``.\tray.bat --restart``
(PowerShell) and ``./tray.bat --restart`` (Git Bash). Starting the bat by its full path from
``subprocess`` needs no shell search and no argument rewriting.

A matching ``git_sha`` alone proves nothing: a restart that never ran leaves a process that
already loaded ``HEAD`` serving, and the sha matches anyway. A process's identity is its
``pid`` when ``/api/version`` reports one, else its ``built_at`` (captured once at process
start by ``src/build_info.py``), so an app needs no extra field to be checked.

Vendor-verbatim: copy this file byte-identical into ``src/`` (manifest key ``restart_check``,
which needs ``build_info``). Self-contained on purpose, standard library only, Python 3.9.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.build_info import resolve_git_sha

_log = logging.getLogger("restart_check")

_REPO_ROOT = Path(__file__).resolve().parent.parent
TRAY_BAT = _REPO_ROOT / "tray.bat"

# A console-less caller (an agent's captured shell, a scheduled task) would otherwise get a
# fresh console window for the bat. Derived here, not imported: this file is vendored verbatim.
_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

DEFAULT_VERSION_PATH = "/api/version"
DEFAULT_VERIFY_TIMEOUT_S = 60.0
# tray_lifecycle.ps1 stops the old tray, waits up to 5 s per held port, sleeps 2 s, then polls
# the new build for 30 s; 180 s is that chain with margin, so a bat past it is hung, not slow.
TRAY_RESTART_TIMEOUT_S = 180.0
_PROBE_TIMEOUT_S = 3.0
_POLL_INTERVAL_S = 1.0

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_CONFIRMED = 2

Identity = dict[str, Any]


def fetch_version(port: int, path: str = DEFAULT_VERSION_PATH) -> Identity | None:
    """``GET <path>`` on the loopback webapp (https, then http), or ``None``."""
    # Loopback only, and a self-signed fleet cert is the normal case.
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    for scheme in ("https", "http"):
        url = f"{scheme}://127.0.0.1:{port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=_PROBE_TIMEOUT_S, context=insecure) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            _log.debug("version probe %s failed: %s", scheme, exc)
            continue
        if isinstance(body, dict):
            return body
    return None


def _identity_of(body: Identity | None) -> tuple[str, str]:
    """``(kind, value)`` naming the serving process, ``("", "")`` when the answer has none."""
    if not body:
        return "", ""
    pid = body.get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool):
        return "pid", str(pid)
    built_at = body.get("built_at")
    if isinstance(built_at, str) and built_at:
        return "built_at", built_at
    return "", ""


def decide(
    before: Identity | None, after: Identity | None, head_sha: str
) -> tuple[bool | None, str]:
    """``(verdict, reason)`` for whether ``after`` is a new process serving ``head_sha``.

    ``True`` is a proven replacement, ``False`` a proven failure to replace (retry while time
    remains), ``None`` that nothing can establish it (not a pass, and not worth polling).
    ``before`` is ``None`` when no webapp was answering, so any answering process is new.
    """
    if after is None:
        return False, "no webapp answering on the port"
    served = str(after.get("git_sha") or "")
    if not served or served == "unknown":
        return False, f"the webapp reports no usable git_sha ({served or 'empty'})"
    if head_sha == "unknown":
        return None, "HEAD could not be resolved, so the served git_sha cannot be compared"
    if not (head_sha.startswith(served) or served.startswith(head_sha)):
        return False, f"webapp serves git_sha {served}, expected HEAD {head_sha}"
    kind, value = _identity_of(after)
    if before is None:
        return True, f"new process ({kind or 'identity unreported'} {value}) serves git_sha {served}"
    old_kind, old_value = _identity_of(before)
    if not kind or not old_kind or kind != old_kind:
        return None, (
            "/api/version reports neither a pid nor a built_at on both sides of the restart, "
            "so a new process cannot be told from the old one"
        )
    if value == old_value:
        return False, f"same process still serving ({kind} {value}): the restart did not replace it"
    return True, f"new process ({kind} {value}, was {old_value}) serves git_sha {served}"


def verify(
    port: int,
    before: Identity | None,
    timeout_s: float = DEFAULT_VERIFY_TIMEOUT_S,
    *,
    fetch: Callable[[int], Identity | None] = fetch_version,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    head_sha: str | None = None,
) -> int:
    """Poll until a new process serves ``HEAD``; the exit code for ``restart``."""
    head = head_sha if head_sha is not None else resolve_git_sha(_REPO_ROOT)
    deadline = clock() + timeout_s
    # request-budget-exempt: a one-shot CLI poll of loopback, capped by timeout_s, that ends the moment a new process answers
    while True:
        verdict, reason = decide(before, fetch(port), head)
        if verdict is True:
            _log.info("✅ verify OK :%s %s", port, reason)
            return EXIT_OK
        if verdict is None:
            _log.error("❌ verify NOT CONFIRMED :%s %s", port, reason)
            return EXIT_NOT_CONFIRMED
        if clock() >= deadline:
            _log.error("❌ verify FAILED :%s %s", port, reason)
            return EXIT_FAILED
        sleep(_POLL_INTERVAL_S)


def _std_handle(stream: Any) -> Any:
    """The caller's own std handle for the child, or DEVNULL when it has none.

    The child runs with no console window, so a handle left to default would be a hidden
    console and the bat's output would never reach the caller.
    """
    try:
        return stream.fileno()
    except (AttributeError, OSError, ValueError):
        return subprocess.DEVNULL


def restart(
    port: int,
    timeout_s: float = DEFAULT_VERIFY_TIMEOUT_S,
    *,
    tray_bat: Path = TRAY_BAT,
    run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    fetch: Callable[[int], Identity | None] | None = None,
    verify_fn: Callable[..., int] | None = None,
    version_path: str = DEFAULT_VERSION_PATH,
) -> int:
    """Read the served identity, run ``tray_bat --restart`` by absolute path, then verify.

    ``version_path`` names where the app serves its identity; an explicit ``fetch`` or
    ``verify_fn`` (a test seam) wins over it.
    """
    bat = tray_bat.resolve()
    if not bat.is_file():
        _log.error("❌ restart NOT CONFIRMED: %s does not exist", bat)
        return EXIT_NOT_CONFIRMED
    probe = fetch if fetch is not None else functools.partial(fetch_version, path=version_path)
    check = verify_fn if verify_fn is not None else functools.partial(verify, fetch=probe)
    before = probe(port)
    kind, value = _identity_of(before)
    _log.info("ℹ️ snapshot :%s %s", port, f"{kind} {value}" if kind else "nothing answering")
    _log.info("ℹ️ restart running %s --restart", bat)
    sys.stdout.flush()
    try:
        done = run(
            [str(bat), "--restart"],
            cwd=str(bat.parent),
            stdin=subprocess.DEVNULL,
            stdout=_std_handle(sys.stdout),
            stderr=_std_handle(sys.stderr),
            timeout=TRAY_RESTART_TIMEOUT_S,
            creationflags=_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.error("❌ restart FAILED: tray.bat did not complete (%s)", exc)
        return EXIT_FAILED
    if done.returncode != 0:
        _log.error("❌ restart FAILED: tray.bat exited %s, so the old process was not replaced", done.returncode)
        return EXIT_FAILED
    return check(port, before, timeout_s)


def _utf8_streams() -> None:
    """Let the ✅/❌ lines through a console whose code page cannot encode them."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.restart_check", description=__doc__)
    parser.add_argument("action", choices=("restart",))
    parser.add_argument("--port", type=int, required=True, help="the webapp's loopback port")
    parser.add_argument("--timeout", type=float, default=DEFAULT_VERIFY_TIMEOUT_S, help="verify seconds")
    parser.add_argument(
        "--version-path", default=DEFAULT_VERSION_PATH,
        help="where the app serves its identity, for an app whose endpoint is not /api/version",
    )
    args = parser.parse_args(argv)
    if not args.version_path.startswith("/"):
        parser.error("--version-path must start with '/'")
    _utf8_streams()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    return restart(args.port, args.timeout, version_path=args.version_path)


if __name__ == "__main__":
    sys.exit(main())
