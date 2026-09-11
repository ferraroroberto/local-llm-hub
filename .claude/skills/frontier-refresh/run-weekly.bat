@echo off
REM Bi-weekly frontier-refresh wrapper, fired by the app-launcher Jobs tab
REM (job id "frontier-refresh", weekly FRI 02:30, visible console).
REM The Jobs scheduler only supports weekly, so bi-weekly is done here:
REM alternate weeks are skipped using a fixed-epoch week counter computed by
REM week_parity.ps1 (next to this file). Its answer is the EXIT CODE, read
REM from %ERRORLEVEL% - never a `for /f` capture of an inline
REM `powershell -Command "..."`: that shape can come back empty (tray.bat
REM documents it under non-interactive callers; a PATH without the
REM WindowsPowerShell dir does it too), and an empty capture used to fall
REM through to a run every week with nothing in any log. Any code other
REM than on/off week now fails the job loudly instead of guessing (#558).
REM Runs the /frontier-refresh skill headless on the local Claude
REM subscription; bypassPermissions because a scheduled run has no human
REM to answer permission prompts. Everything inside the skill runs
REM synchronously (fleet-config#314) - a headless claude -p session that
REM backgrounds a step and ends its turn dies silently. --verbose streams
REM turn-by-turn activity so the visible console shows live progress.

set "PS=C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
"%PS%" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0week_parity.ps1"
set "WEEKPARITY_RC=%ERRORLEVEL%"
if "%WEEKPARITY_RC%"=="11" (
    echo Off week - bi-weekly skip, next run fires next week.
    exit /b 0
)
if not "%WEEKPARITY_RC%"=="10" (
    echo ERROR: week parity unknown - week_parity.ps1 exited %WEEKPARITY_RC%, expected 10 or 11. Not running.
    exit /b 2
)

cd /d E:\automation\local-llm-hub
claude -p "/frontier-refresh" --model claude-sonnet-5 --effort high --permission-mode bypassPermissions --verbose
