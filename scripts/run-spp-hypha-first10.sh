#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${HYPHA_SPP_DATA_ROOT:?Set HYPHA_SPP_DATA_ROOT to the pinned SPP data directory}"
: "${HYPHA_MODEL:?Set HYPHA_MODEL to the solver model}"

benchmark_id="${1:?Pass one SPP benchmark id}"
run_id="${HYPHA_SPP_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-first10}"
output_dir="outputs/spp/${run_id}/hypha/${benchmark_id}"

uv run python -m hypha_exp.benchmarks.spp_runner \
  "${benchmark_id}" \
  --start 0 \
  --limit 10 \
  --output "${output_dir}" \
  --run-id "${run_id}"
