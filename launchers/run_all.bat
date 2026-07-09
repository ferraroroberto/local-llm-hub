@echo off
REM ==========================================================
REM  local-llm-hub - start every locally-launchable backend in
REM  its own console window. The roster is NOT hardcoded here:
REM  it is derived live from config/models.yaml by
REM  `run_backend --list-launchable`, so it always reflects the
REM  active host's `enabled:` contract (owned, enabled, non-virtual
REM  rows only). Remote-owned and disabled models are skipped.
REM  Close each window individually to stop.
REM ==========================================================
cd /d "%~dp0.."

REM Enumerate the hub + every backend this host can actually spawn,
REM then open one console window per id via run_backend (identical
REM to the per-model launchers). `for /f` captures the id list on
REM stdout; run_backend logs to stderr so this stays clean.
for /f "usebackq delims=" %%m in (`.venv\Scripts\python.exe -m src.run_backend --list-launchable`) do (
    start "Local LLM Hub - %%m" cmd /k .venv\Scripts\python.exe -m src.run_backend %%m
)

echo Launched the hub + every locally-launchable backend (derived from config/models.yaml) in separate windows.
echo (Models owned by another host, disabled on this host, or virtual are skipped.)
