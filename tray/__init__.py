"""System-tray launcher for the local-LLM hub.

Thin wrapper around :mod:`src.server_process` and :mod:`src.backend_process`:
the tray itself owns no business logic — it only drives those modules from
a pystray menu and a tkinter log window. Entry point is :mod:`tray.__main__`,
launched silently from ``tray.bat`` via ``pythonw.exe``.
"""
