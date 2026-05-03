@echo off
chcp 65001 >nul
REM ============================================================================
REM  claude-local-calls - tray launcher
REM ----------------------------------------------------------------------------
REM  Resident system-tray icon. Starts the FastAPI hub on :8000 and (if
REM  configured) the autostart model from `config/models.yaml -> tray:`.
REM  Drop a shortcut to this file in the Windows Startup folder for
REM  always-on local-LLM hosting.
REM ============================================================================

setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PYW=%SCRIPT_DIR%.venv\Scripts\pythonw.exe"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"

cd /d "%SCRIPT_DIR%" || exit /b 1

REM Prefer pythonw.exe so no console window stays open.
if exist "%VENV_PYW%" (
    start "" "%VENV_PYW%" -m tray
) else if exist "%VENV_PY%" (
    start "" "%VENV_PY%" -m tray
) else (
    start "" pythonw -m tray
)
exit /b 0
