#!/usr/bin/env bash
# local-llm-hub - Kokoro TTS server on :8095 (OpenAI /v1/audio/speech)
# Kokoro-82M ONNX comparison option.
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m src.run_backend kokoro
