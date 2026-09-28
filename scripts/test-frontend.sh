#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
frontend_dir="$repo_root/apps/frontend"

cd "$frontend_dir"
uv run --frozen pytest tests "$@"
