#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mcp_dir="$repo_root/services/mcp"
reset=false

usage() {
  cat <<'USAGE'
Uso:
  ./scripts/run-mcp.sh [--reset]

Arranca el MCP de asientos en http://127.0.0.1:${MCP_PORT:-8080}/mcp con el
layout de demostración (services/mcp/layouts/morcillaconf-demo-v1.json).

Opciones:
  --reset     Vacía la base de asientos antes de arrancar: sala libre.
  -h, --help  Muestra esta ayuda.

Variables opcionales: SEATING_LAYOUT_FILE, SEATING_LAYOUT_ID,
SEATING_DATABASE_PATH, SEATING_HOLD_MINUTES (5), MCP_HOST, MCP_PORT (8080).
USAGE
}

while (($# > 0)); do
  case "$1" in
    --reset) reset=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Opción desconocida: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

layout_file="${SEATING_LAYOUT_FILE:-$mcp_dir/layouts/morcillaconf-demo-v1.json}"
layout_file="$(cd "$(dirname "$layout_file")" && pwd)/$(basename "$layout_file")"
database_path="${SEATING_DATABASE_PATH:-$mcp_dir/data/seating.db}"

cd "$mcp_dir"
# The hash comes from the service's own canonical JSON.
SEATING_LAYOUT_JSON="$(uv run --frozen python -m restaurant_mcp.layout "$layout_file" --json)"
SEATING_LAYOUT_SHA256="$(uv run --frozen python -m restaurant_mcp.layout "$layout_file" --sha256)"
export SEATING_LAYOUT_JSON SEATING_LAYOUT_SHA256
export SEATING_LAYOUT_ID="${SEATING_LAYOUT_ID:-$(basename "$layout_file" .json)}"
export SEATING_DATABASE_PATH="$database_path"
export SEATING_HOLD_MINUTES="${SEATING_HOLD_MINUTES:-5}"
export MCP_HOST="${MCP_HOST:-0.0.0.0}"
export MCP_PORT="${MCP_PORT:-8080}"

if [[ "$reset" == true ]]; then
  rm -f "$database_path" "$database_path-wal" "$database_path-shm" "$database_path-journal"
  printf 'Base de asientos vaciada: %s\n' "$database_path"
fi

printf 'MCP de asientos: layout %s, bloqueos de %s min, http://127.0.0.1:%s/mcp\n' \
  "$SEATING_LAYOUT_ID" "$SEATING_HOLD_MINUTES" "$MCP_PORT"
exec uv run --frozen python -m restaurant_mcp.main
