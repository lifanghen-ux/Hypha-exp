#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export DOCKER_DEFAULT_PLATFORM="${DOCKER_DEFAULT_PLATFORM:-linux/amd64}"
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
exec uv run python -m hypha_exp.benchmarks.harbor_budget_cli \
  run -c configs/lhtb/pi_real_three_task_budget.yaml --yes
