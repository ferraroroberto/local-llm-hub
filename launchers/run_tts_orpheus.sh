#!/usr/bin/env bash
# local-llm-hub - Orpheus TTS server on :8093 (OpenAI /v1/audio/speech)
# Expressive LLM/SNAC voice, no longer the audio_speech default.
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m src.run_backend orpheus
