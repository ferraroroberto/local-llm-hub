"""Thin wrapper around the `gemini` CLI.

Shells out to Google's official Gemini CLI in non-interactive mode and
returns an envelope shaped like :func:`src.claude_cli.call_claude`'s
output, so the hub's response-translation helpers can be shared.

Auth follows whatever `gemini` has cached locally — typically a browser
login via `gemini /auth login`, which uses the Google account and any
Google AI Pro / Ultra quota attached to it. If ``GEMINI_API_KEY`` is set
in the environment, the CLI uses that instead.

Unlike `claude -p --output-format json`, the Gemini CLI's non-interactive
mode emits plain text on stdout. We build the envelope ourselves and
leave token counts at zero, since the CLI does not surface them.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


class GeminiCLIError(RuntimeError):
    pass


def call_gemini(
    prompt: str,
    *,
    model: Optional[str] = None,
    system: Optional[str] = None,
    images: Optional[Sequence[Path]] = None,
    timeout: float = 600.0,
) -> Dict[str, Any]:
    """Invoke `gemini -p` and return an envelope matching the Claude shape.

    The prompt is sent on stdin to avoid command-line length limits. Images
    are referenced inline via ``@<absolute path>`` tokens prepended to the
    prompt — the CLI's standard file-injection syntax. ``system`` is folded
    into the prompt as a leading instruction block, since the Gemini CLI's
    non-interactive flag does not accept a separate system-prompt argument.
    """
    # Resolve the binary explicitly: on Windows, npm installs `gemini.cmd`
    # (a shell shim), and CreateProcess with shell=False does not consult
    # PATHEXT, so a bare "gemini" raises FileNotFoundError even when the
    # CLI is on PATH.
    exe = shutil.which("gemini")
    if not exe:
        raise GeminiCLIError(
            "`gemini` CLI not found on PATH. Install with "
            "`npm i -g @google/gemini-cli` and run `gemini /auth login` once."
        )
    # `--skip-trust`: bypass the CLI's trusted-folder check, which would
    # otherwise abort headless runs from an "untrusted" cwd.
    # `-p " "`: the CLI requires a non-empty prompt value after `-p`; the
    # real prompt is piped on stdin and appended to this placeholder.
    args: List[str] = [exe, "--skip-trust", "-p", " "]
    if model:
        args += ["-m", model]

    pieces: List[str] = []
    if system:
        pieces.append(f"[System]\n{system}\n")
    # Gemini CLI sandboxes file reads to its workspace (cwd). When images
    # are attached we run the subprocess with `cwd` set to their parent
    # dir and reference each file by basename — absolute paths outside
    # the workspace come back as "file path is inaccessible due to
    # security constraints". The hub's `_extract_image_blocks` writes
    # every image for a request into the same temp dir, so a single
    # parent always covers the whole batch.
    run_cwd: Optional[str] = None
    if images:
        image_paths = [Path(p).resolve() for p in images]
        run_cwd = str(image_paths[0].parent)
        refs = " ".join(f"@{p.name}" for p in image_paths)
        pieces.append(refs)
    pieces.append(prompt)
    stdin_text = "\n".join(pieces)

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            args,
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
            shell=False,
            cwd=run_cwd,
            creationflags=creationflags,
        )
    except FileNotFoundError as e:
        raise GeminiCLIError(
            "`gemini` CLI not found on PATH. Install with "
            "`npm i -g @google/gemini-cli` and run `gemini /auth login` once."
        ) from e

    if proc.returncode != 0:
        raise GeminiCLIError(
            f"gemini -p exited {proc.returncode}: {(proc.stderr or '')[:500]}"
        )

    text = (proc.stdout or "").strip()
    if not text:
        raise GeminiCLIError("empty stdout from gemini -p")

    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
