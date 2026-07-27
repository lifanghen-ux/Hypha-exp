#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
uv venv --python 3.12.11 --clear .venv
uv pip install -e .
uv pip install -e benchmarks/LHTB/harbor
uv run python scripts/verify-lhtb-images.py
