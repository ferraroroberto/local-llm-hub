#!/usr/bin/env bash
# Stop the local Langfuse stack (issue #284). Idempotent.
set -euo pipefail
cd "$(dirname "$0")"
docker compose -f docker/langfuse/docker-compose.yml down
echo
echo "Langfuse stack stopped."
