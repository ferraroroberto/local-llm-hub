@echo off
chcp 65001 >nul
REM ============================================================================
REM  LOCAL LLM HUB TRAY - tray icon that owns the FastAPI hub lifecycle
REM ----------------------------------------------------------------------------
REM  Resident system-tray icon. Starts the FastAPI hub on :8000 and (if
REM  configured) the autostart models from `config/models.yaml -> tray:`.
REM  Drop a shortcut to this file in the Windows Startup folder for
REM  always-on local-LLM hosting.
REM
REM  Idempotent:
REM    tray.bat              -> no-op if a Local LLM Hub tray is already running
REM    tray.bat --restart    -> stop the running tray (and its tree: the hub on
REM                             :8000) and start a fresh one
REM
REM  Detection matches the tray process by command line + this project's .venv
REM  path via CIM, then kills BY PID with /T. We never blanket-kill pythonw, so
REM  sister-app trays (AppLauncher, PhotoOCR, VoiceTranscriber, ...) and any
REM  other unrelated python processes are untouched.
REM
REM  --restart is orphan-proof: besides killing the tray subtree, it reclaims
REM  this app's hub port :8000 by its owning PID, regardless of process
REM  parentage. The hub runs in a separate process (`-m src.server`) that can
REM  detach from the tray; a stale one would otherwise survive a subtree kill,
REM  block the fresh hub from binding, and keep serving the old build while the
REM  restart reports success. The reclaim is scoped by CommandLine (not the
REM  process image path): a venv-launched pythonw re-execs the base interpreter,
REM  so .Path can report the shared base python while CommandLine still carries
REM  the .venv path. Matching the image path would miss the real hub; the
REM  CommandLine scope keeps the sweep on THIS repo's children only.
REM  See project-scaffolding#29.
REM
REM  IMPORTANT: port :8090 (whisper-server) is mutex-SHARED with the sibling
REM  voice-transcriber/transcribe_voice, and the llama-server model ports
REM  (8081/8082/8086/8087) are separate native exes. None are reclaimed here.
REM  Only :8000, which this tray definitively owns, is reclaimed.
REM ============================================================================

setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv\Scripts"
set "VENV_PYW=%VENV_DIR%\pythonw.exe"
set "VENV_PY=%VENV_DIR%\python.exe"

cd /d "%SCRIPT_DIR%" || exit /b 1

set "WANT_RESTART="
if /i "%~1"=="--restart" set "WANT_RESTART=1"
if /i "%~1"=="-r"        set "WANT_RESTART=1"

set "PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
set "TRAY_VENV=%SCRIPT_DIR%.venv"
set "TRAY_PS=%SCRIPT_DIR%tray\tray_lifecycle.ps1"
if not exist "%TRAY_PS%" (
    echo ERROR: missing tray helper "%TRAY_PS%" -- vendor tray\tray_lifecycle.ps1 from the scaffold.
    exit /b 1
)
set "TRAY_PIDS="
for /f "usebackq delims=" %%P in (`%PS% -NoProfile -NonInteractive -File "%TRAY_PS%" detect -VenvDir "%TRAY_VENV%" -TrayMatch "-m\s+tray"`) do (
    if defined TRAY_PIDS (set "TRAY_PIDS=!TRAY_PIDS! %%P") else (set "TRAY_PIDS=%%P")
)

if defined TRAY_PIDS if not defined WANT_RESTART (
    echo Local LLM Hub tray is already running ^(PID: !TRAY_PIDS!^).
    echo Run "tray.bat --restart" to stop it and start fresh.
    exit /b 0
)

if defined WANT_RESTART (
    if defined TRAY_PIDS (
        echo Stopping previous Local LLM Hub tray ^(PID: !TRAY_PIDS!^)...
        for %%P in (!TRAY_PIDS!) do (
            taskkill /T /F /PID %%P >nul 2>&1
        )
    )
    REM Orphan-proof: reclaim the hub port :8000 from ANY holder whose
    REM CommandLine is under this repo's .venv, even one detached from the tray
    REM subtree above. We match on CommandLine (not the process image path):
    REM a venv-launched pythonw re-execs the base interpreter, so .Path reports
    REM the shared base python while CommandLine still carries the .venv path.
    REM NOTE: :8090 (whisper, mutex-shared with voice-transcriber) and the
    REM llama-server model ports are intentionally NOT reclaimed.
    set "RECLAIM_VENV=%SCRIPT_DIR%.venv"
    %PS% -NoProfile -NonInteractive -File "%TRAY_PS%" reclaim -VenvDir "!RECLAIM_VENV!" -Ports "8000"
    REM Give Windows a moment to release :8000 before rebinding.
    ping 127.0.0.1 -n 3 >nul
)

REM Prefer pythonw.exe so no console window stays open. The window title
REM differentiates this tray from sister apps' trays.
if exist "%VENV_PYW%" (
    start "Local LLM Hub Tray" "%VENV_PYW%" -m tray
) else if exist "%VENV_PY%" (
    start "Local LLM Hub Tray" "%VENV_PY%" -m tray
) else (
    start "Local LLM Hub Tray" pythonw -m tray
)
exit /b 0
