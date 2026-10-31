"""Telegram alerts for the resource watch: delivery and the per-condition debounce.

The hub has no Telegram client of its own and must not grow one: the fleet's
one transport is ``notify_send`` (in ``~/.claude/hooks``), which resolves the
bot token and the destination chat from fleet config. The hub runs it as a
subprocess with its own interpreter, so no new recipient, chat or credential
exists in this repo. The message goes in on stdin, which the transport decodes
as UTF-8, instead of through a command line.

Debounce is one alert per condition per ``cooldown_hours`` (6 h), counted from
the last *successful* send and persisted, so neither a restart nor a long
outage re-sends it. A failed delivery does not start the cooldown: it is
retried after ``retry_after_s`` and recorded as a failure, never as sent.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..no_window import NO_WINDOW
from .resource_store import ResourceStore

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SCRIPT = Path.home() / ".claude" / "hooks" / "notify_send.py"

# send(text) -> (delivered, error). Injectable so tests never reach Telegram.
Sender = Callable[[str, Dict[str, Any]], Tuple[bool, Optional[str]]]


def send_via_notify_script(text: str, cfg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Deliver ``text`` through the fleet transport (``notify_send``); ``(False, why)`` on any failure."""
    script = Path(cfg["script"]) if cfg.get("script") else DEFAULT_SCRIPT
    if not script.is_file():
        return False, f"notify script not found: {script}"
    try:
        # Run from the repo root: the transport picks the chat from its working project.
        done = subprocess.run(
            [sys.executable, str(script), "--category", str(cfg["category"])],
            input=text.encode("utf-8"), capture_output=True, timeout=float(cfg["timeout_s"]),
            cwd=PROJECT_ROOT, creationflags=NO_WINDOW, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"notify script failed to run: {exc}"
    if done.returncode != 0:
        tail = done.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:] or ["no output"]
        return False, f"notify script exited {done.returncode}: {tail[0]}"
    return True, None


def message_for(condition: Dict[str, Any], host_label: str) -> str:
    """The one alert line. Plain text, one line, names the machine and the drive."""
    return f"{host_label}: {condition['reason']}"


class AlertGate:
    """Decides which firing conditions may send now, and sends them."""

    def __init__(self, store: ResourceStore, send: Optional[Sender] = None) -> None:
        self._store = store
        self._send = send

    def _due(self, key: str, now: float, cfg: Dict[str, Any]) -> Optional[str]:
        """``None`` when ``key`` may send now, else why it must stay quiet."""
        state = self._store.alert_state(key)
        if state["last_sent"] is not None and now - state["last_sent"] < float(cfg["cooldown_hours"]) * 3600.0:
            return "debounced"
        if state["last_attempt"] is not None and now - state["last_attempt"] < float(cfg["retry_after_s"]):
            return "retry-wait"
        return None

    def process(self, conditions: List[Dict[str, Any]], cfg: Dict[str, Any], *, now: float,
                host_label: str) -> List[Dict[str, Any]]:
        """Send each firing condition that is due; return one event per delivery attempt."""
        events: List[Dict[str, Any]] = []
        if not cfg["enabled"]:
            return events
        for cond in conditions:
            if cond["state"] != "alert" or self._due(cond["key"], now, cfg) is not None:
                continue
            send = self._send or send_via_notify_script
            delivered, error = send(message_for(cond, host_label), cfg)
            self._store.record_alert(cond["key"], ts=now, sent=delivered, error=error)
            if delivered:
                logger.warning("🚨 resource watch: alert sent: %s", cond["reason"])
            else:
                logger.warning("⚠️ resource watch: alert NOT delivered (%s): %s", error, cond["reason"])
            events.append({"ts": now, "key": cond["key"], "reason": cond["reason"],
                           "delivery": "sent" if delivered else "failed", "error": error})
        return events
