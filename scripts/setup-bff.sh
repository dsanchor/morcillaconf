#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bff_dir="$repo_root/apps/bff"

cd "$bff_dir"
if [[ -f uv.lock ]]; then
  uv sync --frozen --python 3.13
else
  printf 'apps/bff/uv.lock no existe todavía: se genera ahora con uv sync.\n'
  uv sync --python 3.13
  printf 'Revisa y versiona apps/bff/uv.lock en el mismo cambio.\n'
fi
