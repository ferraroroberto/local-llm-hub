@echo off
REM Bi-weekly frontier-refresh wrapper, fired by the app-launcher Jobs tab
REM (job id "frontier-refresh", weekly FRI 02:30, visible console).
REM The Jobs scheduler only supports weekly, so bi-weekly is done here:
REM alternate weeks are skipped using a fixed-epoch week counter (whole
REM weeks since Monday 2026-01-05, mod 2 - PS 5.1-safe, no ISOWeek type,
REM and immune to the 53-week-year parity glitch of ISO week numbers).
REM Runs the /frontier-refresh skill headless on the local Claude
REM subscription; bypassPermissions because a scheduled run has no human
REM to answer permission prompts. Everything inside the skill runs
REM synchronously (fleet-config#314) - a headless claude -p session that
REM backgrounds a step and ends its turn dies silently. --verbose streams
REM turn-by-turn activity so the visible console shows live progress.

for /f %%w in ('powershell -NoProfile -Command "[math]::Floor(((Get-Date).Date - [datetime]::new(2026,1,5)).Days / 7) %% 2"') do set WEEKPARITY=%%w
if "%WEEKPARITY%"=="1" echo Off week - bi-weekly skip, next run fires next week.
if "%WEEKPARITY%"=="1" exit /b 0

cd /d E:\automation\local-llm-hub
claude -p "/frontier-refresh" --model claude-sonnet-5 --effort high --permission-mode bypassPermissions --verbose
