"""Tkinter log viewer — one tab per process (hub + each enabled local model).

Polls the existing in-memory ring buffers (``server_process.log_lines()``,
``backend_process.log_lines(model_id)``) every 500 ms and appends only the
new tail to the per-tab Text widget. No file I/O, no log re-implementation.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional

from src import backend_process as bp
from src import server_process as sp
from src import system_stats as stats
from src.model_registry import Model, enabled_models

logger = logging.getLogger(__name__)

POLL_MS = 500
MAX_TAB_LINES = 1000  # mirrors the ring-buffer cap upstream


class _Tab:
    """One notebook tab bound to a (name, fetch_fn) pair."""

    def __init__(self, parent: ttk.Notebook, title: str, fetch: Callable[[], list[str]]) -> None:
        self.title = title
        self.fetch = fetch
        self.frame = ttk.Frame(parent)
        self.text = tk.Text(
            self.frame,
            wrap="none",
            height=28,
            width=120,
            background="#101418",
            foreground="#d8dee4",
            insertbackground="#d8dee4",
            font=("Consolas", 9),
        )
        scroll_y = ttk.Scrollbar(self.frame, orient="vertical", command=self.text.yview)
        scroll_x = ttk.Scrollbar(self.frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set, state="disabled")
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        self.frame.rowconfigure(0, weight=1)
        self.frame.columnconfigure(0, weight=1)
        self.last_count = 0
        self.autoscroll = True

    def refresh(self) -> None:
        try:
            lines = self.fetch()
        except Exception as exc:
            logger.debug("log fetch failed for %s: %s", self.title, exc)
            return
        if len(lines) < self.last_count:
            # Buffer was cleared (e.g. process restart) — replace content.
            self._replace(lines)
            return
        new = lines[self.last_count:]
        if not new:
            return
        self.text.configure(state="normal")
        self.text.insert("end", "\n".join(new) + "\n")
        # Cap visible length to MAX_TAB_LINES so the widget doesn't grow forever.
        line_count = int(self.text.index("end-1c").split(".")[0])
        if line_count > MAX_TAB_LINES:
            self.text.delete("1.0", f"{line_count - MAX_TAB_LINES}.0")
        self.text.configure(state="disabled")
        if self.autoscroll:
            self.text.see("end")
        self.last_count = len(lines)

    def _replace(self, lines: list[str]) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if lines:
            self.text.insert("end", "\n".join(lines) + "\n")
        self.text.configure(state="disabled")
        if self.autoscroll:
            self.text.see("end")
        self.last_count = len(lines)


class LogWindow:
    """Toplevel that streams hub + per-model logs and refreshes resource stats."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.title("claude-local-calls — logs")
        self.win.geometry("1100x650")
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_layout()

        self._tabs: Dict[str, _Tab] = {}
        self._add_tab("hub", "🛰 Hub", sp.log_lines)
        for model in enabled_models():
            if model.backend in ("openai", "whisper"):
                self._add_tab(model.id, f"🧠 {model.display_name}", _model_log_fetcher(model))

        self._poll_after_id: Optional[str] = None
        self._schedule_poll()

    # ------------------------------------------------------------------ build

    def _build_layout(self) -> None:
        container = ttk.Frame(self.win, padding=6)
        container.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True)

        bottom = ttk.Frame(container, padding=(2, 6, 2, 0))
        bottom.pack(fill="x")

        self.status_var = tk.StringVar(value="(starting…)")
        ttk.Label(bottom, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True)

        self.autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bottom,
            text="Autoscroll",
            variable=self.autoscroll_var,
            command=self._sync_autoscroll,
        ).pack(side="right")

    def _add_tab(self, key: str, title: str, fetch: Callable[[], list[str]]) -> None:
        tab = _Tab(self.notebook, title, fetch)
        self.notebook.add(tab.frame, text=title)
        self._tabs[key] = tab

    # ------------------------------------------------------------------- poll

    def _schedule_poll(self) -> None:
        self._poll_after_id = self.win.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        if not self.win.winfo_exists():
            return
        for tab in self._tabs.values():
            tab.refresh()
        self.status_var.set(_status_line())
        self._schedule_poll()

    def _sync_autoscroll(self) -> None:
        value = self.autoscroll_var.get()
        for tab in self._tabs.values():
            tab.autoscroll = value

    # ------------------------------------------------------------------ close

    def show(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def _on_close(self) -> None:
        # Hide instead of destroying — re-opening from the tray menu is cheap.
        if self._poll_after_id is not None:
            try:
                self.win.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None
        self.win.withdraw()

    def reopen(self) -> None:
        if self._poll_after_id is None:
            self._schedule_poll()
        self.show()


def _model_log_fetcher(model: Model) -> Callable[[], list[str]]:
    def fetch() -> list[str]:
        return bp.log_lines(model.id)
    return fetch


def _status_line() -> str:
    parts: list[str] = []
    parts.append("hub: " + ("✅ running" if sp.is_reachable(timeout=0.4) else "⏸ stopped"))
    running = sorted(bp.running_backends().keys())
    if running:
        parts.append("models: " + ", ".join(running))
    else:
        parts.append("models: (none)")

    ram = stats.ram_stats()
    parts.append(f"RAM {ram['used_gb']:.1f}/{ram['total_gb']:.1f} GB ({ram['percent']:.0f}%)")

    gpus = stats.gpu_stats()
    for gpu in gpus:
        used = gpu.get("used_mb")
        total = gpu.get("total_mb")
        util = gpu.get("util_percent")
        if used is not None and total is not None:
            parts.append(
                f"GPU {used / 1024:.1f}/{total / 1024:.1f} GB ({util:.0f}% util)"
                if util is not None
                else f"GPU {used / 1024:.1f}/{total / 1024:.1f} GB"
            )
    return "  ·  ".join(parts)
