"""TrayApp: pystray icon + tk event loop wired to the hub/backend modules.

All heavy lifting (start/stop processes, log capture, registry lookups,
resource probes) is delegated to :mod:`src.server_process`,
:mod:`src.backend_process`, :mod:`src.model_registry`, and
:mod:`src.system_stats`. This module only owns the UX glue: tray menu,
event queue, autostart sequence, and quit cleanup.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from typing import Optional

import pystray

from src import backend_process as bp
from src import server_process as sp
from src.model_registry import Model, enabled_models

from .config import TrayConfig
from .icon import COLOR_RUNNING, COLOR_STARTING, COLOR_STOPPED, make_icon_image
from .log_window import LogWindow

try:
    from winotify import Notification as _WinToast  # type: ignore
except ImportError:
    _WinToast = None

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Internal queued events dispatched on the tk main thread.
EVT_OPEN_WINDOW = "open_window"
EVT_OPEN_STREAMLIT = "open_streamlit"
EVT_START_HUB = "start_hub"
EVT_STOP_HUB = "stop_hub"
EVT_TOGGLE_MODEL = "toggle_model"
EVT_REFRESH = "refresh"
EVT_QUIT = "quit"


class TrayApp:
    def __init__(self, cfg: TrayConfig) -> None:
        self.cfg = cfg
        self.events: queue.Queue = queue.Queue()

        # Hidden tk root so we can use Toplevel + after() from the tray.
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        self._icon: Optional[pystray.Icon] = None
        self._log_window: Optional[LogWindow] = None
        self._streamlit_proc: Optional[subprocess.Popen] = None
        # Serialise "Open Streamlit admin" — every menu click runs in a
        # fresh worker thread, and without a lock rapid clicks each end
        # up calling webbrowser.open() (the bug that opened a cascade
        # of empty browser windows).
        self._streamlit_lock = threading.Lock()

        # Snapshot enabled models — registry doesn't change at runtime.
        # Local models are toggleable; the Claude row (subscription, no
        # local process) is kept for display only.
        all_enabled = enabled_models()
        self._models: list[Model] = [
            m for m in all_enabled if m.backend in ("openai", "whisper")
        ]
        self._claude_model: Optional[Model] = next(
            (m for m in all_enabled if m.backend == "claude"), None
        )

    # --------------------------------------------------------------- run / quit

    def run(self) -> int:
        self._icon = pystray.Icon(
            "claude_local_calls_tray",
            make_icon_image(COLOR_STARTING),
            "claude-local-calls",
            menu=self._build_menu(),
        )
        self._icon.run_detached()
        self.root.after(80, self._pump_events)
        self.root.after(2000, self._refresh_icon_color)
        if self.cfg.autostart_hub:
            threading.Thread(target=self._autostart_worker, daemon=True).start()
        try:
            self.root.mainloop()
        finally:
            self._shutdown()
        return 0

    def _shutdown(self) -> None:
        # Stop every model we started, then the hub. Best-effort — never
        # block quitting on a hung subprocess.
        for model_id in list(bp.running_backends().keys()):
            try:
                bp.stop(model_id)
            except Exception as exc:
                logger.debug("stop(%s) failed: %s", model_id, exc)
        if sp.is_running():
            try:
                sp.stop()
            except Exception as exc:
                logger.debug("hub stop failed: %s", exc)
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        # pystray's Win32 message-pump thread sometimes refuses to unwind
        # cleanly. Force-exit once subprocesses are down.
        os._exit(0)

    # ----------------------------------------------------------------- menu

    def _build_menu(self) -> pystray.Menu:
        items: list[pystray.MenuItem] = []
        if self._claude_model is not None:
            items.append(pystray.MenuItem(
                f"☁ {self._claude_model.display_name}  (always on, subscription)",
                None,
                enabled=False,
            ))
            items.append(pystray.Menu.SEPARATOR)
        items.extend(self._build_model_item(m) for m in self._models)
        if not items:
            items.append(pystray.MenuItem(
                "(no models enabled on this host)", None, enabled=False,
            ))
        models_submenu = pystray.Menu(*items)

        def hub_label(_item: pystray.MenuItem) -> str:
            own = sp.ownership()
            if own == sp.OWNERSHIP_OURS:
                return f"🛰 Hub  {sp.BASE_URL}"
            if own == sp.OWNERSHIP_EXTERNAL:
                ext = sp.external_pid()
                tag = f"adopted, PID {ext}" if ext else "adopted"
                return f"🛰 Hub  {sp.BASE_URL}  ({tag})"
            return "🛰 Hub  (stopped)"

        return pystray.Menu(
            pystray.MenuItem(hub_label, None, enabled=False),
            pystray.MenuItem(
                "▶ Start hub",
                lambda: self._enqueue(EVT_START_HUB),
                enabled=lambda _i: not sp.is_running(),
            ),
            pystray.MenuItem(
                "■ Stop hub",
                lambda: self._enqueue(EVT_STOP_HUB),
                enabled=lambda _i: sp.is_running(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🧠 Models", models_submenu),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🪟 Open log window", lambda: self._enqueue(EVT_OPEN_WINDOW), default=True),
            pystray.MenuItem("🌐 Open Streamlit admin", lambda: self._enqueue(EVT_OPEN_STREAMLIT)),
            pystray.MenuItem("🔄 Refresh", lambda: self._enqueue(EVT_REFRESH)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self._enqueue(EVT_QUIT)),
        )

    def _build_model_item(self, model: Model) -> pystray.MenuItem:
        # Bind model.id through an outer-function closure. pystray inspects
        # action arity and only accepts 0- or 2-arg callables, so the
        # `lambda mid=…:` default-arg trick fails — we wrap each callback
        # in a factory instead.
        model_id = model.id

        def on_toggle() -> None:
            self._enqueue((EVT_TOGGLE_MODEL, model_id))

        def is_checked(_item: pystray.MenuItem) -> bool:
            # Reachable counts as "on" — covers both our managed process
            # and an externally-running adopted instance.
            return bp.ownership(model_id) != bp.OWNERSHIP_NONE

        return pystray.MenuItem(
            f"{model.display_name}  (:{model.port})",
            on_toggle,
            checked=is_checked,
        )

    # ----------------------------------------------------------- event loop

    def _enqueue(self, event) -> None:
        self.events.put(event)

    def _pump_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(80, self._pump_events)

    def _handle_event(self, event) -> None:
        if isinstance(event, tuple):
            kind = event[0]
        else:
            kind = event

        if kind == EVT_OPEN_WINDOW:
            self._open_log_window()
        elif kind == EVT_OPEN_STREAMLIT:
            threading.Thread(target=self._open_streamlit, daemon=True).start()
        elif kind == EVT_START_HUB:
            threading.Thread(target=self._start_hub_worker, daemon=True).start()
        elif kind == EVT_STOP_HUB:
            threading.Thread(target=self._stop_hub_worker, daemon=True).start()
        elif kind == EVT_TOGGLE_MODEL:
            _, model_id = event
            threading.Thread(target=self._toggle_model_worker, args=(model_id,), daemon=True).start()
        elif kind == EVT_REFRESH:
            self._update_menu()
            self._refresh_icon_color()
        elif kind == EVT_QUIT:
            self.root.quit()

    # ------------------------------------------------------------- workers

    def _autostart_worker(self) -> None:
        if not sp.is_running():
            ok, msg = sp.start()
            self._notify("Hub", f"✅ {msg}" if ok else f"⚠️  {msg}")
        if not self._wait_hub_ready(self.cfg.hub_ready_timeout_s):
            self._notify("Hub", "⚠️  not reachable after {:.0f}s".format(self.cfg.hub_ready_timeout_s))
            self._refresh_icon_color()
            self._update_menu()
            return
        self._refresh_icon_color()
        self._update_menu()

        if self.cfg.autostart_model:
            self._start_model(self.cfg.autostart_model, autostart=True)

    def _start_hub_worker(self) -> None:
        ok, msg = sp.start()
        self._notify("Hub", f"✅ {msg}" if ok else f"⚠️  {msg}")
        self._wait_hub_ready(self.cfg.hub_ready_timeout_s)
        self._refresh_icon_color()
        self._update_menu()

    def _stop_hub_worker(self) -> None:
        ok, msg = sp.stop()
        self._notify("Hub", "■ Stopped" if ok else f"⚠️  {msg}")
        self._refresh_icon_color()
        self._update_menu()

    def _toggle_model_worker(self, model_id: str) -> None:
        own = bp.ownership(model_id)
        if own == bp.OWNERSHIP_OURS:
            ok, msg = bp.stop(model_id)
            self._notify(model_id, "■ Stopped" if ok else f"⚠️  {msg}")
        elif own == bp.OWNERSHIP_EXTERNAL:
            # Don't surprise-kill someone else's process from a tray click.
            # The Streamlit Models tab exposes "Stop external" for this.
            self._notify(
                model_id,
                "ℹ already running externally — use the Streamlit Models "
                "tab to take over",
            )
        else:
            self._start_model(model_id, autostart=False)
        self._update_menu()

    def _start_model(self, model_id: str, *, autostart: bool) -> None:
        ok, msg = bp.start(model_id)
        prefix = "(autostart) " if autostart else ""
        if not ok:
            self._notify(f"{prefix}{model_id}", f"⚠️  {msg}")
            return
        # Adopt path: bp.start() returned True without spawning because
        # the port already answers. Skip the readiness wait.
        if "adopted" in msg:
            self._notify(f"{prefix}{model_id}", f"✅ {msg}")
            return
        self._notify(f"{prefix}{model_id}", "▶ Starting…")
        # llama-server takes 10–60 s to load weights — surface readiness
        # on a separate thread so the menu stays responsive.
        threading.Thread(
            target=self._wait_model_ready_worker,
            args=(model_id,),
            daemon=True,
        ).start()

    def _wait_model_ready_worker(self, model_id: str) -> None:
        model = next((m for m in self._models if m.id == model_id), None)
        if model is None:
            return
        deadline = time.time() + 120.0
        while time.time() < deadline:
            if not bp.is_running(model_id):
                self._notify(model_id, "⚠️  process exited before becoming reachable")
                self._update_menu()
                return
            if bp.is_reachable(model, timeout=0.8):
                self._notify(model_id, "✅ Ready")
                self._update_menu()
                return
            time.sleep(1.0)
        self._notify(model_id, "⚠️  not reachable after 120s")
        self._update_menu()

    # --------------------------------------------------------- helpers / UI

    def _wait_hub_ready(self, timeout_s: float) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if sp.is_reachable(timeout=0.5):
                return True
            time.sleep(0.5)
        return False

    def _open_log_window(self) -> None:
        if self._log_window is None or not self._log_window.win.winfo_exists():
            self._log_window = LogWindow(self.root)
        else:
            self._log_window.reopen()

    def _open_streamlit(self) -> None:
        url = "http://localhost:8501"
        # Reject overlapping clicks. Without this, every menu click runs
        # in its own thread and each calls webbrowser.open() once
        # Streamlit binds — a cascade of empty browser windows.
        if not self._streamlit_lock.acquire(blocking=False):
            return
        try:
            # Already up (we spawned it earlier, or someone else did).
            if _port_in_use(8501):
                webbrowser.open(url)
                return
            # Reuse a still-running spawn from a previous click.
            spawn_needed = (
                self._streamlit_proc is None
                or self._streamlit_proc.poll() is not None
            )
            if spawn_needed:
                try:
                    creationflags = (
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        | subprocess.CREATE_NO_WINDOW
                        if sys.platform == "win32" else 0
                    )
                    self._streamlit_proc = subprocess.Popen(
                        [sys.executable, "-m", "streamlit", "run", "app/app.py",
                         "--server.headless", "true",
                         "--browser.gatherUsageStats", "false"],
                        cwd=str(PROJECT_ROOT),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creationflags,
                    )
                except Exception as exc:
                    self._notify("Streamlit", f"⚠️  failed to launch: {exc}")
                    return
            # Wait up to ~10 s for Streamlit to bind, then open one tab.
            for _ in range(20):
                if _port_in_use(8501):
                    webbrowser.open(url)
                    return
                time.sleep(0.5)
            self._notify("Streamlit", "⚠️  did not bind on :8501 within 10s")
        finally:
            self._streamlit_lock.release()

    def _refresh_icon_color(self) -> None:
        if self._icon is None:
            return
        if sp.is_reachable(timeout=0.4):
            color = COLOR_RUNNING
        elif sp.is_running():
            color = COLOR_STARTING
        else:
            color = COLOR_STOPPED
        try:
            self._icon.icon = make_icon_image(color)
        except Exception:
            pass

    def _update_menu(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.update_menu()
        except Exception as exc:
            logger.debug("update_menu failed: %s", exc)

    def _notify(self, title: str, message: str) -> None:
        if _WinToast is not None:
            try:
                _WinToast(app_id="claude-local-calls", title=title, msg=message).show()
                return
            except Exception as exc:
                logger.debug("winotify failed (%s) — falling back to pystray", exc)
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
                return
            except Exception:
                pass
        logger.info("🔔 %s: %s", title, message)


# --------------------------------------------------------------- helpers

def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()
