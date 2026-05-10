@echo off
REM ==========================================================
REM  local-llm-hub - whisper-medium-translate (eager CPU, port 8091)
REM  OpenAI-compatible /v1/audio/transcriptions; supports
REM  task=translate. Sibling to run_whisper.bat (turbo on 8090).
REM  Loads ggml-medium.bin upfront and stays resident (~1.5 GB RAM).
REM ==========================================================
title Local LLM Hub - whisper-translate
cd /d "%~dp0.."

echo ============================================================
echo   whisper-medium-translate on http://127.0.0.1:8091
echo   POST WAV to /v1/audio/transcriptions (task=translate to translate)
echo   ggml-medium.bin loaded on CPU (~1.5 GB RAM, always ready)
echo   Ctrl+C to stop
echo ============================================================
echo.

.venv\Scripts\python.exe -m src.run_backend whisper_translate
pause
