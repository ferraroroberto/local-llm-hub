#!/usr/bin/env bash
# local-llm-hub - whisper-medium-translate (lazy, :8091)
# Sibling to run_whisper.sh (turbo on :8090). Whisper-server child
# is spawned on first request and torn down after the idle window.
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m src.run_backend whisper_translate
