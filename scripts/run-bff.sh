#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bff_dir="$repo_root/apps/bff"

cd "$bff_dir"
# One worker: turns run in-process and the state lives in SQLite.
uv run --frozen uvicorn bff.main:create_app --factory \
  --host "${BFF_HOST:-0.0.0.0}" \
  --port "${BFF_PORT:-8000}" \
  --workers 1 \
  "$@"
