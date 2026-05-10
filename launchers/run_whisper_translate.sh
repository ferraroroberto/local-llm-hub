#!/usr/bin/env bash
# local-llm-hub - whisper-medium-translate (eager CPU, :8091)
# Sibling to run_whisper.sh (turbo on :8090). Loads ggml-medium.bin
# upfront and stays resident (~1.5 GB RAM).
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m src.run_backend whisper_translate
