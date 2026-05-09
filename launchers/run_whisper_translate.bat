@echo off
REM ==========================================================
REM  local-llm-hub - whisper-medium-translate (lazy, port 8091)
REM  OpenAI-compatible /v1/audio/transcriptions; supports
REM  task=translate. Sibling to run_whisper.bat (turbo on 8090).
REM  The whisper-server child is spawned on first request and
REM  torn down after the configured idle window (5 min default).
REM ==========================================================
title Local LLM Hub - whisper-translate (lazy)
cd /d "%~dp0.."

echo ============================================================
echo   whisper_translate proxy on http://127.0.0.1:8091
echo   POST WAV to /v1/audio/transcriptions (task=translate to translate)
echo   First call cold-loads ggml-medium.bin on CPU (~3-5s)
echo   Idle 5 min unloads the model; next call cold-loads again
echo   Ctrl+C to stop
echo ============================================================
echo.

.venv\Scripts\python.exe -m src.run_backend whisper_translate
pause
