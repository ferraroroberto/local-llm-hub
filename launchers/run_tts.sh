#!/usr/bin/env bash
# local-llm-hub - Orpheus TTS server on :8093 (OpenAI /v1/audio/speech)
# The audio_speech role; auto-loaded by the tray.
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m src.run_backend orpheus
