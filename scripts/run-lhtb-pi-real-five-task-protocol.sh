#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
uv run harbor run -c configs/lhtb/pi_real_five_task_protocol.yaml --yes
