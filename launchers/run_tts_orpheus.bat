@echo off
REM ==========================================================
REM  local-llm-hub - Orpheus TTS server (port 8093, on demand)
REM  OpenAI-compatible /v1/audio/speech (text -> speech).
REM  Expressive LLM/SNAC voice, no longer the audio_speech default.
REM ==========================================================
title Local LLM Hub - orpheus-tts
cd /d "%~dp0.."

echo ============================================================
echo   tts_server: orpheus-tts on http://127.0.0.1:8093
echo   POST text to /v1/audio/speech
echo   Ctrl+C to stop
echo ============================================================
echo.

.venv\Scripts\python.exe -m src.run_backend orpheus
pause
