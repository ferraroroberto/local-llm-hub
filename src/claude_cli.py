"""Thin wrapper around the `claude -p` CLI.

Shells out to the Claude Code CLI in headless JSON mode and returns the
parsed envelope. Uses the user's local Claude auth — no API key required.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


class ClaudeCLIError(RuntimeError):
    pass


def call_claude(
    prompt: str,
    *,
    model: Optional[str] = None,
    system: Optional[str] = None,
    images: Optional[Sequence[Path]] = None,
    timeout: float = 600.0,
) -> Dict[str, Any]:
    """Invoke `claude -p --output-format json` and return the parsed envelope.

    Prompt is fed via stdin to avoid command-line length limits. ``images``
    are passed via ``--add-dir`` (the temp dir holding them is added to
    Claude's allowed filesystem set) and their absolute paths are prepended
    to the prompt so Claude knows to read them.
    """
    args: List[str] = ["claude", "-p", "--output-format", "json"]
    if model:
        args += ["--model", model]
    if system:
        args += ["--system-prompt", system]

    if images:
        # All images live under a single per-request temp dir today; pass
        # that one parent dir via --add-dir and reference each file by
        # absolute path in the prompt.
        parents = {Path(p).resolve().parent for p in images}
        for d in parents:
            args += ["--add-dir", str(d)]
        refs = "\n".join(f"- {Path(p).resolve()}" for p in images)
        prompt = f"Attached images:\n{refs}\n\n{prompt}"

    try:
        # Suppress the Windows Terminal window that would otherwise spawn
        # for every request when the hub itself is running under pythonw
        # (e.g. launched from the tray with CREATE_NO_WINDOW — children
        # don't inherit the parent's no-window state).
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=creationflags,
        )
    except FileNotFoundError as e:
        raise ClaudeCLIError(
            "`claude` CLI not found on PATH. Install Claude Code first."
        ) from e

    if proc.returncode != 0:
        raise ClaudeCLIError(
            f"claude -p exited {proc.returncode}: {proc.stderr[:500]}"
        )

    raw = (proc.stdout or "").strip()
    if not raw:
        raise ClaudeCLIError("empty stdout from claude -p")

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ClaudeCLIError(f"could not parse claude -p JSON: {raw[:200]!r}") from e

    if envelope.get("is_error"):
        raise ClaudeCLIError(
            f"claude -p returned is_error=true: {str(envelope)[:300]}"
        )
    return envelope
