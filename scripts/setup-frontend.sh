#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
frontend_dir="$repo_root/apps/frontend"

cd "$frontend_dir"
if [[ -f uv.lock ]]; then
  uv sync --frozen --python 3.13
else
  printf 'apps/frontend/uv.lock no existe todavía: se genera ahora con uv sync.\n'
  uv sync --python 3.13
  printf 'Revisa y versiona apps/frontend/uv.lock en el mismo cambio.\n'
fi
