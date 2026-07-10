@echo off
chcp 65001 >nul
REM ============================================================================
REM  LOCAL LLM HUB TRAY - tray icon that owns the FastAPI hub lifecycle
REM ----------------------------------------------------------------------------
REM  CANONICAL TEMPLATE (project-scaffolding tray.bat.template), adapted for
REM  this app. Copy to `tray.bat` in a tray-resident app, then replace the four
REM  __PLACEHOLDER__ tokens (marked `=== ADAPT ===`). Everything else is the
REM  orphan-proof reclaim-then-start machinery and is copied verbatim, so a
REM  filled-in copy is byte-identical to every sister tray. Full reasoning:
REM  scaffold docs/windows-tray.md + project-scaffolding#29.
REM
REM  Launch this on login (Startup folder) for an always-on service.
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
REM  The full detect -> kill -> reclaim -> start -> verify lifecycle lives in
REM  tray\tray_lifecycle.ps1 (a committed helper shelled to with -File), NOT in
REM  cmd-side `for /f` output capture or inline `powershell -Command "..."`.
REM  Both cmd shapes have failed under non-interactive nested callers (Git Bash
REM  -> `cmd /c "tray.bat --restart"`, or a finisher skill's Bash tool): detect
REM  output came back empty, nothing was killed, and --restart silently
REM  degraded to a plain start that adopted the stale webapp and reported
REM  success. Delegating once to PowerShell makes behavior identical from any
REM  caller and lets stale git_sha verification fail loudly
REM  (project-scaffolding#54).
REM
REM  --restart is orphan-proof: besides killing the tray subtree, it reclaims
REM  this app's hub port :8000 by its owning PID, regardless of process
REM  parentage. The hub runs in a separate process (`-m src.server`) that can
REM  detach from the tray; a stale one would otherwise survive a subtree kill,
REM  block the fresh hub from binding, and keep serving the old build while the
REM  restart reports success. The reclaim is scoped by CommandLine (not the
REM  process image path): a venv-launched pythonw re-execs the base
REM  interpreter, so .Path can report the shared base python while CommandLine
REM  still carries the .venv path. Matching the image path would miss the real
REM  hub; the CommandLine scope keeps the sweep on THIS repo's children only.
REM  See project-scaffolding#29.
REM
REM  IMPORTANT: port :8090 (whisper-server) is mutex-SHARED with the sibling
REM  voice-transcriber/transcribe_voice, and the llama-server model ports
REM  (8081/8082/8086/8087/8088) are separate native exes. None of these are
REM  ever reclaimed here -- only :8000, which this tray definitively owns, is
REM  in the reclaim list below.
REM ============================================================================

setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"

cd /d "%SCRIPT_DIR%" || exit /b 1

REM === ADAPT (1/4): short app name, used in messages + the start window title ===
set "APP_NAME=Local LLM Hub"
REM === ADAPT (2/4): the args python is started with to launch the tray ===
set "TRAY_LAUNCH=-m tray"

set "WANT_RESTART="
if /i "%~1"=="--restart" set "WANT_RESTART=1"
if /i "%~1"=="-r"        set "WANT_RESTART=1"

REM === ADAPT (3/4): in the -TrayMatch below, a regex matching THIS app's tray
REM     invocation ===
set "PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
set "TRAY_VENV=%SCRIPT_DIR%.venv"
set "TRAY_PS=%SCRIPT_DIR%tray\tray_lifecycle.ps1"
if not exist "%TRAY_PS%" (
    echo ERROR: missing tray helper "%TRAY_PS%" -- vendor tray\tray_lifecycle.ps1 from the scaffold.
    exit /b 1
)

REM === ADAPT (4/4): this tray's exclusively-owned ports as a comma list.
REM     Exclude any mutex-shared port -- :8090 (whisper) and the llama-server
REM     backend ports (8081/8082/8086/8087/8088) are NOT owned by this tray. ===
set "OWNED_PORTS=8000"
REM This app's version endpoint is /admin/api/version, not the default /api/version.
set "VERSION_URL=http://127.0.0.1:8000/admin/api/version"

set "RESTART_ARG="
if defined WANT_RESTART set "RESTART_ARG=-Restart"

REM %~dp0 always ends in a trailing backslash. Passed bare as a quoted
REM cmd-to-exe argument (`-ScriptDir "%SCRIPT_DIR%"`), that trailing `\"`
REM is parsed as an escaped literal quote rather than a closing quote, so
REM the argument never terminates and swallows the rest of the command
REM line. Strip the trailing backslash before quoting to avoid it.
set "SCRIPT_DIR_ARG=%SCRIPT_DIR:~0,-1%"

%PS% -NoProfile -NonInteractive -File "%TRAY_PS%" launch -AppName "%APP_NAME%" -ScriptDir "%SCRIPT_DIR_ARG%" -VenvDir "%TRAY_VENV%" -TrayMatch "-m\s+tray" -Ports "%OWNED_PORTS%" -TrayLaunch "%TRAY_LAUNCH%" -VersionUrl "%VERSION_URL%" !RESTART_ARG!
exit /b %ERRORLEVEL%
