#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
frontend_dir="$repo_root/apps/frontend"

cd "$frontend_dir"
export FRONTEND_BFF_CLIENT="${FRONTEND_BFF_CLIENT:-fake}"
uv run --frozen streamlit run src/frontend/app.py \
  --server.port "${FRONTEND_PORT:-8501}" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false \
  "$@"
