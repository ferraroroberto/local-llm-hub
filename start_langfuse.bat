@echo off
REM Start the local Langfuse stack (issue #4). Idempotent; safe to re-run.
REM First run pulls ~3GB of images; subsequent runs are seconds.
REM
REM Once running: open http://localhost:3000, create a user + project,
REM copy the public/secret keys into the project root .env so the hub's
REM /admin Telemetry tab can talk back to Langfuse.

setlocal
cd /d "%~dp0"
docker compose -f docker\langfuse\docker-compose.yml up -d
if errorlevel 1 (
  echo.
  echo Langfuse stack failed to start. Is Docker Desktop running?
  exit /b 1
)
echo.
echo Langfuse is starting at http://localhost:3000
echo OTLP receiver listening on :4317 (gRPC) and :4318 (HTTP)
endlocal
