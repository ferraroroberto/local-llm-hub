@echo off
REM ==========================================================
REM  local-llm-hub - Chatterbox TTS server (port 8092, on demand)
REM  OpenAI-compatible /v1/audio/speech (text -> speech).
REM  On-demand alternate to the default orpheus voice; carries an
REM  emotion/"tone" dial (exaggeration + cfg_weight) and optional
REM  zero-shot voice cloning.
REM ==========================================================
title Local LLM Hub - chatterbox-tts
cd /d "%~dp0.."

echo ============================================================
echo   tts_server: chatterbox-tts on http://127.0.0.1:8092
echo   POST text to /v1/audio/speech
echo   Ctrl+C to stop
echo ============================================================
echo.

.venv\Scripts\python.exe -m src.run_backend chatterbox
pause
