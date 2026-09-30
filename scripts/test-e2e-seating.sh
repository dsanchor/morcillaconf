#!/usr/bin/env bash
# Seating end to end without Foundry or its knowledge base: the seating MCP,
# the standalone waiter (scripted model, test code only), the BFF with
# BFF_WAITER=remote and no MCP client, and the view's HTTP client, each on its
# own temporary database.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mcp_port="${E2E_MCP_PORT:-18080}"
agent_port="${E2E_AGENT_PORT:-18088}"
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
  for _ in $(seq 1 180); do
    if curl -s -o /dev/null "$url"; then
      return 0
    fi
    sleep 0.5
  done
  printf '%s no arrancó; su registro:\n' "$name" >&2
  cat "$log" >&2
  exit 1
}

show_logs() {
  for name in mcp agent bff; do
    printf '\n--- %s ---\n' "$name" >&2
    tail -n 60 "$work/$name.log" >&2 || true
  done
}

SEATING_DATABASE_PATH="$work/seating.db" MCP_HOST=127.0.0.1 MCP_PORT="$mcp_port" \
  "$repo_root/scripts/run-mcp.sh" >"$work/mcp.log" 2>&1 &
pids+=("$!")
wait_for "http://127.0.0.1:$mcp_port/mcp" "El MCP de asientos" "$work/mcp.log"

(
  cd "$repo_root/agents/restaurant/src/restaurant_agent"
  # No knowledge base: empty values override a developer's .env.
  PORT="$agent_port" \
    FOUNDRY_PROJECT_ENDPOINT="https://scripted.invalid/api/projects/scripted" \
    AZURE_AI_MODEL_DEPLOYMENT_NAME="scripted" \
    MEMORY_DATABASE_PATH="$work/agent-memory.db" \
    SEATING_MCP_URL="http://127.0.0.1:$mcp_port/mcp" \
    AZURE_SEARCH_ENDPOINT="" \
    KNOWLEDGE_BASE_NAME="" \
    OTEL_SDK_DISABLED=true \
    exec uv run --frozen python "$repo_root/tests/end_to_end/scripted_agent_server.py"
) >"$work/agent.log" 2>&1 &
pids+=("$!")
wait_for "http://127.0.0.1:$agent_port/readiness" "El camarero" "$work/agent.log"

(
  cd "$repo_root/apps/bff"
  # No SEATING_MCP_URL: the waiter is the only client of the seating MCP.
  exec env -u SEATING_MCP_URL BFF_WAITER=remote \
    WAITER_AGENT_URL="http://127.0.0.1:$agent_port" \
    BFF_DATABASE_PATH="$work/bff.db" MEMORY_DATABASE_PATH="$work/memory.db" \
    uv run --frozen uvicorn bff.main:create_app --factory \
      --host 127.0.0.1 --port "$bff_port" --workers 1
) >"$work/bff.log" 2>&1 &
pids+=("$!")
wait_for "http://127.0.0.1:$bff_port/healthz" "El BFF" "$work/bff.log"

cd "$repo_root/apps/frontend"
if ! env -u SEATING_MCP_URL E2E_BFF_URL="http://127.0.0.1:$bff_port" \
  uv run --frozen pytest -p no:cacheprovider "$repo_root/tests/end_to_end" "$@"; then
  show_logs
  exit 1
fi
