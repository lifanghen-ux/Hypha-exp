#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export DOCKER_DEFAULT_PLATFORM="${DOCKER_DEFAULT_PLATFORM:-linux/amd64}"
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
uv run harbor run -c configs/lhtb/formal_compare_llm.yaml --yes
