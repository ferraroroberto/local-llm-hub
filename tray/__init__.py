"""Local LLM Hub system tray — single-file implementation in ``tray.py``.

Replaces the old multi-file (``app.py`` + ``log_window.py`` + ``config.py``)
layout. The tk log window is gone — logs now live in the /admin webapp's
Hub tab inside the hub's own FastAPI process.
"""
