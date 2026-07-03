#!/usr/bin/env bash
# local-llm-hub - start every locally-launchable backend; each in its own
# process group so individual Ctrl+C works. Pids are printed so you can
# `kill` them; or just close the terminal.
#
# The roster is NOT hardcoded here: it is derived live from
# config/models.yaml by `run_backend --list-launchable`, so it always
# reflects the active host's `enabled:` contract (owned, enabled,
# non-virtual rows only). Remote-owned and disabled models are skipped.
set -uo pipefail
cd "$(dirname "$0")/.."

start() {
  local name="$1"
  ./.venv/bin/python -m src.run_backend "$name" &
  echo "  $name pid=$!"
}

echo "launching the hub + every locally-launchable backend (derived from config/models.yaml)..."
echo "(models owned by another host, disabled on this host, or virtual are skipped)"

# Enumerate the hub + every backend this host can spawn, then start each.
# run_backend logs to stderr, so stdout is a clean one-id-per-line list.
while IFS= read -r name; do
  [ -n "$name" ] && start "$name"
done < <(./.venv/bin/python -m src.run_backend --list-launchable)

wait
