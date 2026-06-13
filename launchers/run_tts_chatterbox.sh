#!/usr/bin/env bash
# local-llm-hub - Chatterbox TTS server on :8092 (OpenAI /v1/audio/speech)
# On-demand alternate to the default orpheus voice (tone dial + voice cloning).
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m src.run_backend chatterbox
