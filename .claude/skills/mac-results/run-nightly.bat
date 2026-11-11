@echo off
REM Nightly Mac-results wrapper, fired by an app-launcher Job (daily, plus a
REM later retry; schedule in docs/mac-results.md). Runs the /mac-results skill
REM headless on the local Claude subscription. A Job is not a launcher-hosted
REM session: the CLI ignores an inherited APP_LAUNCHER_SESSION_ID, delivers
REM through the Job outbox and keeps unsent images in the Job's artifacts
REM (docs/mac-results.md). bypassPermissions because a scheduled
REM run has no human to answer prompts; the skill runs one foreground pass
REM (fleet-config#314).
REM
REM A headless `claude -p` exits 0 whatever its pass reported, so the pass's
REM own exit code is read back from a status file the CLI writes
REM (MAC_RESULTS_STATUS_FILE). A missing or non-numeric file means the pass
REM never ran to the end: exit 3 ("not confirmed"), never 0.
REM Exit: the CLI's code (0 delivered or waiting, 1 unreadable/refused/not
REM confirmed, 2 not configured); 3 pass not confirmed; else claude's own.

setlocal
pushd "%~dp0..\..\.." || exit /b 3

set "MAC_RESULTS_STATUS_FILE=%TEMP%\claude\mac-results\status.txt"
if exist "%MAC_RESULTS_STATUS_FILE%" del /q "%MAC_RESULTS_STATUS_FILE%"

call claude -p "/mac-results" --model claude-sonnet-5 --effort low --permission-mode bypassPermissions --verbose
set "CLAUDE_RC=%ERRORLEVEL%"
if not "%CLAUDE_RC%"=="0" (
    echo ERROR: claude exited %CLAUDE_RC% before the pass was confirmed.
    popd
    exit /b %CLAUDE_RC%
)

if not exist "%MAC_RESULTS_STATUS_FILE%" (
    echo ERROR: no status file - the pass did not run to the end. Not confirmed.
    popd
    exit /b 3
)
set "PASS_RC="
set /p PASS_RC=<"%MAC_RESULTS_STATUS_FILE%"
echo(%PASS_RC%| findstr /r /x "[0-9]" >nul
if errorlevel 1 (
    echo ERROR: status file unreadable. Not confirmed.
    popd
    exit /b 3
)
echo Mac results pass exited %PASS_RC%.
popd
exit /b %PASS_RC%
