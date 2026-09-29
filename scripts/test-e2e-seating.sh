#!/usr/bin/env bash
# Seating end to end without Foundry: the MCP, the BFF with the scripted waiter
# and the view's HTTP client, each on its own temporary database.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mcp_port="${E2E_MCP_PORT:-18080}"
bff_port="${E2E_BFF_PORT:-18000}"
work="$(mktemp -d)"
pids=()

cleanup() {
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

wait_for() {
  local url="$1" name="$2" log="$3"
  for _ in $(seq 1 120); do
    if curl -s -o /dev/null "$url"; then
      return 0
    fi
    sleep 0.5
  done
  printf '%s no arrancó; su registro:\n' "$name" >&2
  cat "$log" >&2
  exit 1
}

SEATING_DATABASE_PATH="$work/seating.db" MCP_HOST=127.0.0.1 MCP_PORT="$mcp_port" \
  "$repo_root/scripts/run-mcp.sh" >"$work/mcp.log" 2>&1 &
pids+=("$!")
wait_for "http://127.0.0.1:$mcp_port/mcp" "El MCP de asientos" "$work/mcp.log"

(
  cd "$repo_root/apps/bff"
  BFF_WAITER=scripted BFF_DATABASE_PATH="$work/bff.db" MEMORY_DATABASE_PATH="$work/memory.db" \
    SEATING_MCP_URL="http://127.0.0.1:$mcp_port/mcp" BFF_ROOM_CACHE_SECONDS=0 \
    exec uv run --frozen uvicorn bff.main:create_app --factory \
      --host 127.0.0.1 --port "$bff_port" --workers 1
) >"$work/bff.log" 2>&1 &
pids+=("$!")
wait_for "http://127.0.0.1:$bff_port/healthz" "El BFF" "$work/bff.log"

cd "$repo_root/apps/frontend"
E2E_BFF_URL="http://127.0.0.1:$bff_port" \
  uv run --frozen pytest -p no:cacheprovider "$repo_root/tests/end_to_end" "$@"
