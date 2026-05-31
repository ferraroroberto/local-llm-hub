"""Wrapper around the Antigravity CLI (`agy`), driven through a Windows ConPTY.

Google deprecated the standalone `gemini` CLI — it stops serving Google
AI Pro / Ultra subscribers on 2026-06-18. Its replacement is the
Antigravity CLI (`agy`). This module shells out to `agy` and returns an
envelope shaped like :func:`src.claude_cli.call_claude`'s output, so the
hub's response-translation helpers stay shared.

Two `agy` quirks shape this code:

1. **`agy -p` print mode is a TUI.** It renders the model's reply to a
   console device and writes nothing to a redirected stdout pipe. Run
   under `subprocess.run` it returns empty. So the hub spawns `agy`
   under a pseudo-console (ConPTY, via ``pywinpty``) and strips the
   ANSI control sequences from the rendered output. In print mode the
   rendered output is just the answer plus a few terminal-init escapes.

2. **`agy` has no per-call model flag.** The model is global persisted
   state, changed only through the interactive ``/model`` picker. The
   switch *does* persist to later separate `agy -p` processes. To serve
   the hub's three Gemini rows (Pro / Flash / Flash-Lite) this module
   switches the persisted model with a short interactive ConPTY session
   whenever the requested model differs from the one last selected,
   then runs print mode for the actual prompt. All calls are serialized
   behind a lock so concurrent requests for different models cannot
   interleave the global model switch.

Auth follows whatever `agy` has cached locally — a silent keyring login
against the Google account and its AI Pro / Ultra quota. No API key.

Token counts are not surfaced by `agy`, so usage is reported as zero —
unchanged from the old `gemini` CLI path.
"""

from __future__ import annotations

import contextlib
import logging
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _tracer():
    try:
        from opentelemetry import trace

        return trace.get_tracer("local_llm_hub.gemini_cli")
    except Exception:  # noqa: BLE001
        return None

logger = logging.getLogger(__name__)


class GeminiCLIError(RuntimeError):
    pass


# `agy` selects its model from global persisted state, not a CLI flag.
# The hub serializes all Gemini calls and remembers which model was last
# selected so it only pays the ~interactive model-switch cost on a change.
_LOCK = threading.Lock()
_current_model: Optional[str] = None

# Strips ANSI/VT escape sequences from ConPTY output: CSI sequences
# (incl. private `?`/`$`/space params and cursor-style `1 q`), simple
# two-char escapes, OSC strings, and charset-designation escapes.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?$ ]*[A-Za-z]"
    r"|\x1b[=>]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[()][AB0]"
)


def _strip_ansi(text: str) -> str:
    """Remove escape sequences and stray control bytes, keep \\n and \\t."""
    text = _ANSI_RE.sub("", text)
    return "".join(c for c in text if c >= " " or c in "\n\t")


def _import_pty():
    """Return ``winpty.PtyProcess`` or raise a clear GeminiCLIError.

    ConPTY is Windows-only. The hub's only Gemini host is the Windows
    reference box; the Mac host enables no `gemini-*` rows.
    """
    if sys.platform != "win32":
        raise GeminiCLIError(
            "the Gemini (Antigravity CLI) backend requires Windows ConPTY; "
            "this host is not win32"
        )
    try:
        from winpty import PtyProcess  # type: ignore
    except ImportError as e:  # pragma: no cover - environment dependent
        raise GeminiCLIError(
            "`pywinpty` is not installed — run `pip install pywinpty` "
            "(it is in requirements.txt) to use the Gemini backend"
        ) from e
    return PtyProcess


def _resolve_agy() -> str:
    exe = shutil.which("agy")
    if not exe:
        raise GeminiCLIError(
            "`agy` (Antigravity CLI) not found on PATH. Install it from "
            "https://antigravity.google and sign in once with your Google "
            "account (the CLI replaces the deprecated `gemini` CLI)."
        )
    return exe


class _Pty:
    """A ConPTY-hosted process with a background reader thread.

    `pywinpty`'s ``read()`` blocks, so a daemon thread drains it into a
    buffer; callers poll :meth:`text` for rendered content.
    """

    def __init__(self, args: List[str], cwd: Optional[str] = None,
                 cols: int = 160, rows: int = 50) -> None:
        PtyProcess = _import_pty()
        self._proc = PtyProcess.spawn(args, dimensions=(rows, cols), cwd=cwd)
        self._buf: List[str] = []
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        while True:
            try:
                data = self._proc.read()
            except (EOFError, OSError):
                break
            if data:
                self._buf.append(data)
            else:
                time.sleep(0.03)

    def text(self) -> str:
        return "".join(self._buf)

    def write(self, keys: str) -> None:
        self._proc.write(keys)

    def alive(self) -> bool:
        return self._proc.isalive()

    def wait_exit(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._proc.isalive():
                self._thread.join(2)
                return True
            time.sleep(0.1)
        return False

    def wait_for(self, markers: Sequence[str], timeout: float) -> Optional[str]:
        """Block until the rendered output contains a marker; return it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rendered = _strip_ansi(self.text())
            for marker in markers:
                if marker in rendered:
                    return marker
            if not self._proc.isalive():
                return None
            time.sleep(0.15)
        return None

    def kill(self) -> None:
        try:
            self._proc.terminate(force=True)
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


def _norm(label: str) -> str:
    """Normalize a model label for comparison (collapse whitespace)."""
    return re.sub(r"\s+", " ", label).strip().lower()


def _parse_picker(rendered: str) -> Tuple[List[str], int]:
    """Parse the `/model` picker screen.

    Returns ``(labels, current_index)`` for the most recent render of
    the "Switch Model" block. The current model is the row tagged
    ``(current)``.
    """
    start = rendered.rfind("Switch Model")
    if start < 0:
        return [], 0
    block = rendered[start:]
    end = block.find("Keyboard:")
    if end > 0:
        block = block[:end]

    labels: List[str] = []
    current = 0
    for line in block.splitlines()[1:]:
        row = line.strip()
        if not row:
            continue
        if row.startswith(">"):
            row = row[1:].strip()
        is_current = row.endswith("(current)")
        if is_current:
            row = row[: -len("(current)")].strip()
        label = re.sub(r"\s+", " ", row).strip()
        # Picker rows always carry a parenthesised effort, e.g. "(High)".
        if not label or "(" not in label:
            continue
        if is_current:
            current = len(labels)
        labels.append(label)
    return labels, current


def _switch_model(exe: str, target: str, timeout: float = 120.0) -> None:
    """Switch `agy`'s globally-selected model to ``target`` via `/model`."""
    pty = _Pty([exe, "--dangerously-skip-permissions"])
    try:
        hit = pty.wait_for(
            ["for shortcuts", "trust this folder", "trust the contents"], 50)
        if hit is None:
            raise GeminiCLIError("agy interactive UI did not become ready")
        if "trust" in hit:
            # Folder-trust dialog: default highlight is "Yes, I trust".
            pty.write("\r")
            if pty.wait_for(["for shortcuts"], 30) is None:
                raise GeminiCLIError("agy did not reach main UI after trust prompt")

        pty.write("/model")
        time.sleep(0.8)
        pty.write("\r")
        if pty.wait_for(["Switch Model"], 25) is None:
            raise GeminiCLIError("agy `/model` picker did not open")
        time.sleep(0.6)

        labels, current = _parse_picker(_strip_ansi(pty.text()))
        if not labels:
            raise GeminiCLIError("could not parse agy `/model` picker")
        norm_target = _norm(target)
        try:
            target_idx = next(
                i for i, lbl in enumerate(labels) if _norm(lbl) == norm_target)
        except StopIteration:
            raise GeminiCLIError(
                f"model {target!r} is not offered by agy; "
                f"available: {', '.join(labels)}"
            )

        delta = target_idx - current
        key = "\x1b[B" if delta > 0 else "\x1b[A"
        for _ in range(abs(delta)):
            pty.write(key)
            time.sleep(0.15)
        time.sleep(0.3)
        pty.write("\r")
        # The confirmation toast reads "Model set to <label>". Selecting
        # the already-current model may not toast — tolerate that.
        pty.wait_for(["Model set to", target], 15)
        logger.info("ℹ️ agy model switched to %s", target)
    finally:
        try:
            pty.write("\x03")
            time.sleep(0.3)
            pty.write("\x03")
            time.sleep(0.3)
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
        pty.kill()


def _print_call(exe: str, prompt: str, cwd: Optional[str],
                 timeout: float) -> str:
    """Run `agy -p` print mode under a ConPTY and return the cleaned reply."""
    print_timeout = max(30, int(timeout))
    args = [
        exe, "-p", prompt,
        "--dangerously-skip-permissions",
        "--print-timeout", f"{print_timeout}s",
    ]
    pty = _Pty(args, cwd=cwd)
    if not pty.wait_exit(timeout + 30):
        pty.kill()
        raise GeminiCLIError(f"agy -p did not finish within {timeout:.0f}s")
    reply = _strip_ansi(pty.text()).strip()
    if not reply:
        raise GeminiCLIError(
            "empty reply from `agy -p` (print mode) — the CLI may be "
            "signed out; run `agy` once interactively to re-authenticate"
        )
    return reply


def call_gemini(
    prompt: str,
    *,
    model: Optional[str] = None,
    system: Optional[str] = None,
    attachments: Optional[Sequence[Path]] = None,
    timeout: float = 600.0,
) -> Dict[str, Any]:
    """Invoke `agy` and return an envelope matching the Claude shape.

    ``model`` is the exact Antigravity CLI picker label (e.g.
    ``"Gemini 3.1 Pro (High)"``). When it differs from the model last
    selected, the globally-persisted model is switched first. ``system``
    is folded into the prompt as a leading instruction block — `agy`
    print mode has no separate system-prompt argument. Attachments
    (images and/or PDF documents) are referenced inline as ``@<basename>``
    tokens and the subprocess runs with ``cwd`` set to their parent dir,
    since the CLI resolves file references against its workspace.
    """
    global _current_model
    exe = _resolve_agy()

    tracer = _tracer()
    cm = (
        tracer.start_as_current_span("gemini_cli.invoke")
        if tracer is not None
        else contextlib.nullcontext(None)
    )
    with cm as span:
        if span is not None and hasattr(span, "set_attribute"):
            try:
                if model:
                    span.set_attribute("gemini_cli.model", model)
                span.set_attribute("gemini_cli.attachments", len(attachments or []))
            except Exception:  # noqa: BLE001
                pass

        with _LOCK:
            model_switched = bool(model and model != _current_model)
            if model_switched:
                if span is not None and hasattr(span, "add_event"):
                    try:
                        span.add_event("model_switch", attributes={"target": model})
                    except Exception:  # noqa: BLE001
                        pass
                _switch_model(exe, model)
                _current_model = model

            pieces: List[str] = []
            if system:
                pieces.append(f"[System]\n{system}\n")
            run_cwd: Optional[str] = None
            if attachments:
                attachment_paths = [Path(p).resolve() for p in attachments]
                run_cwd = str(attachment_paths[0].parent)
                pieces.append(" ".join(f"@{p.name}" for p in attachment_paths))
            pieces.append(prompt)
            full_prompt = "\n".join(pieces)

            reply = _print_call(exe, full_prompt, run_cwd, timeout)

        if span is not None and hasattr(span, "set_attribute"):
            try:
                span.set_attribute("gemini_cli.reply_bytes", len(reply))
                span.set_attribute("gemini_cli.model_switched", model_switched)
            except Exception:  # noqa: BLE001
                pass

    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": reply,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
