@echo off
REM ==========================================================
REM  local-llm-hub - Kokoro TTS server (port 8095, on demand)
REM  OpenAI-compatible /v1/audio/speech (text -> speech).
REM  Kokoro-82M ONNX comparison option.
REM ==========================================================
title Local LLM Hub - kokoro-tts
cd /d "%~dp0.."

echo ============================================================
echo   tts_server: kokoro-tts on http://127.0.0.1:8095
echo   POST text to /v1/audio/speech
echo   Ctrl+C to stop
echo ============================================================
echo.

.venv\Scripts\python.exe -m src.run_backend kokoro
pause
