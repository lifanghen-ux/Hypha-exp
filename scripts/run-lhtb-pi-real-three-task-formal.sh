#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export DOCKER_DEFAULT_PLATFORM="${DOCKER_DEFAULT_PLATFORM:-linux/amd64}"
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

job_dir="outputs/lhtb/hypha-exp-lhtb-pi-real-three-task-formal-v2"
uv run python -m hypha_exp.benchmarks.harbor_budget_cli \
  run -c configs/lhtb/pi_real_three_task_formal.yaml --yes
uv run python scripts/summarize_pi_real_protocol.py "$job_dir"
