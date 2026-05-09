#!/usr/bin/env bash
# local-llm-hub - llama-server for Gemma 4 26B-A4B IT on :8087
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m src.run_backend gemma4_26b
