#!/usr/bin/env bash
# local-llm-hub - llama-server for Qwen 3.5 4B on :8088
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m src.run_backend qwen35_4b
