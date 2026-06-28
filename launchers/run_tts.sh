#!/usr/bin/env bash
# local-llm-hub - Piper TTS server on :8096 (OpenAI /v1/audio/speech)
# The audio_speech role; auto-loaded by the tray.
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m src.run_backend piper
