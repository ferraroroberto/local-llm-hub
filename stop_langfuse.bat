@echo off
REM Stop the local Langfuse stack (issue #284). Idempotent; safe to re-run.
REM Containers stop but named volumes (Postgres data, etc.) are kept —
REM `start_langfuse.bat` picks up right where this left off.

setlocal
cd /d "%~dp0"
docker compose -f docker\langfuse\docker-compose.yml down
if errorlevel 1 (
  echo.
  echo Langfuse stack failed to stop. Is Docker Desktop running?
  exit /b 1
)
echo.
echo Langfuse stack stopped.
endlocal
