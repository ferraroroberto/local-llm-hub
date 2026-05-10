@echo off
REM ==========================================================
REM  local-llm-hub - llama-server for Qwen 3.5 4B (port 8088)
REM  4B hybrid Gated DeltaNet + sparse MoE; full GPU offload.
REM  Q4_K_M GGUF, ~3 GB.
REM ==========================================================
title Local LLM Hub - qwen3.5-4b
cd /d "%~dp0.."

echo ============================================================
echo   llama-server: qwen3.5-4b on http://127.0.0.1:8088/v1
echo   Ctrl+C to stop
echo ============================================================
echo.

.venv\Scripts\python.exe -m src.run_backend qwen35_4b
pause
