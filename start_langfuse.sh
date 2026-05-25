#!/usr/bin/env bash
# Start the local Langfuse stack (issue #4). Idempotent.
set -euo pipefail
cd "$(dirname "$0")"
docker compose -f docker/langfuse/docker-compose.yml up -d
echo
echo "Langfuse is starting at http://localhost:3000"
echo "OTLP receiver listening on :4317 (gRPC) and :4318 (HTTP)"
