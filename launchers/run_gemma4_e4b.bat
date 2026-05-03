@echo off
REM ==========================================================
REM  claude-local-calls - llama-server for Gemma 4 E4B IT (port 8086)
REM  8B dense; full GPU offload. Q4_K_M GGUF, ~5 GB.
REM ==========================================================
title claude-local-calls - gemma4-e4b-it
cd /d "%~dp0.."

echo ============================================================
echo   llama-server: gemma4-e4b-it on http://127.0.0.1:8086/v1
echo   Ctrl+C to stop
echo ============================================================
echo.

.venv\Scripts\python.exe -m src.run_backend gemma4_e4b
pause
