#!/usr/bin/env bash
# local-llm-hub - start every enabled backend; each in its own
# process group so individual Ctrl+C works. Pids are printed so you
# can `kill` them; or just close the terminal.
set -uo pipefail
cd "$(dirname "$0")/.."

start() {
  local name="$1"; shift
  ./.venv/bin/python -m src.run_backend "$name" &
  echo "  $name pid=$!"
}

echo "launching hub + qwen + glm + gemma4-e4b + gemma4-26b-a4b + whisper + whisper-translate (disabled backends will exit immediately)..."
start hub
start qwen
start glm
start gemma4_e4b
start gemma4_26b
start whisper
start whisper_translate
wait
