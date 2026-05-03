@echo off
REM ==========================================================
REM  claude-local-calls - llama-server for Gemma 4 26B-A4B IT (port 8087)
REM  MoE 25.2B total / 3.8B active; IQ4_XS GGUF, ~13.4 GB; full GPU offload.
REM  Context capped at 8K to leave VRAM headroom for KV cache.
REM ==========================================================
title claude-local-calls - gemma4-26b-a4b-it
cd /d "%~dp0.."

echo ============================================================
echo   llama-server: gemma4-26b-a4b-it on http://127.0.0.1:8087/v1
echo   First load takes a minute.
echo   Ctrl+C to stop
echo ============================================================
echo.

.venv\Scripts\python.exe -m src.run_backend gemma4_26b
pause
