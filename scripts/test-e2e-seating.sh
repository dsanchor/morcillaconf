#!/usr/bin/env bash
# Seating end to end without Foundry or its knowledge base: the seating MCP,
# the standalone waiter (scripted model, test code only), the BFF with its
# remote adapter and no MCP client, and the view's HTTP client, each on its own
# temporary database. A second waiter and BFF add the A2A kitchen and cashier
# apps (scripted chef, cashier priced from the versioned carta) for the bill.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mcp_port="${E2E_MCP_PORT:-18080}"
agent_port="${E2E_AGENT_PORT:-18088}"
bff_port="${E2E_BFF_PORT:-18000}"
kitchen_port="${E2E_KITCHEN_PORT:-18089}"
cashier_port="${E2E_CASHIER_PORT:-18090}"
cashier_agent_port="${E2E_CASHIER_AGENT_PORT:-18087}"
cashier_bff_port="${E2E_CASHIER_BFF_PORT:-18001}"
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
  for name in mcp agent bff kitchen cashier cashier-agent cashier-bff; do
    printf '\n--- %s ---\n' "$name" >&2
    tail -n 60 "$work/$name.log" >&2 || true
  done
}

UV_PROJECT_ENVIRONMENT="$work/mcp-venv" \
  SEATING_DATABASE_PATH="$work/seating.db" MCP_HOST=127.0.0.1 MCP_PORT="$mcp_port" \
  "$repo_root/scripts/run-mcp.sh" >"$work/mcp.log" 2>&1 &
pids+=("$!")
wait_for "http://127.0.0.1:$mcp_port/mcp" "El MCP de asientos" "$work/mcp.log"

(
  cd "$repo_root/agents/restaurant/src/restaurant_agent"
  # No knowledge base: empty values override a developer's .env.
  UV_PROJECT_ENVIRONMENT="$work/agent-venv" PORT="$agent_port" \
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
  exec env -u SEATING_MCP_URL \
    UV_PROJECT_ENVIRONMENT="$work/bff-venv" \
    WAITER_AGENT_URL="http://127.0.0.1:$agent_port" \
    BFF_DATABASE_PATH="$work/bff.db" \
    uv run --frozen uvicorn bff.main:create_app --factory \
      --host 127.0.0.1 --port "$bff_port" --workers 1
) >"$work/bff.log" 2>&1 &
pids+=("$!")
wait_for "http://127.0.0.1:$bff_port/healthz" "El BFF" "$work/bff.log"

# The cashier stack: the A2A kitchen runs in the waiter's environment, which
# includes the kitchen project; the cashier in its own.
(
  cd "$repo_root/agents/restaurant/src/restaurant_agent"
  UV_PROJECT_ENVIRONMENT="$work/agent-venv" PORT="$kitchen_port" OTEL_SDK_DISABLED=true \
    exec uv run --frozen python "$repo_root/tests/end_to_end/scripted_kitchen_server.py"
) >"$work/kitchen.log" 2>&1 &
pids+=("$!")
wait_for "http://127.0.0.1:$kitchen_port/health" "La cocina A2A" "$work/kitchen.log"

(
  cd "$repo_root/agents/cashier/src/cashier_agent"
  UV_PROJECT_ENVIRONMENT="$work/cashier-venv" PORT="$cashier_port" OTEL_SDK_DISABLED=true \
    exec uv run --frozen python "$repo_root/tests/end_to_end/scripted_cashier_server.py"
) >"$work/cashier.log" 2>&1 &
pids+=("$!")
wait_for "http://127.0.0.1:$cashier_port/health" "La caja A2A" "$work/cashier.log"

(
  cd "$repo_root/agents/restaurant/src/restaurant_agent"
  UV_PROJECT_ENVIRONMENT="$work/agent-venv" PORT="$cashier_agent_port" \
    FOUNDRY_PROJECT_ENDPOINT="https://scripted.invalid/api/projects/scripted" \
    AZURE_AI_MODEL_DEPLOYMENT_NAME="scripted" \
    MEMORY_DATABASE_PATH="$work/cashier-agent-memory.db" \
    SEATING_MCP_URL="http://127.0.0.1:$mcp_port/mcp" \
    AZURE_SEARCH_ENDPOINT="" \
    KNOWLEDGE_BASE_NAME="" \
    KITCHEN_A2A_URL="http://127.0.0.1:$kitchen_port" \
    CASHIER_A2A_URL="http://127.0.0.1:$cashier_port" \
    OTEL_SDK_DISABLED=true \
    exec uv run --frozen python "$repo_root/tests/end_to_end/scripted_agent_server.py"
) >"$work/cashier-agent.log" 2>&1 &
pids+=("$!")
wait_for "http://127.0.0.1:$cashier_agent_port/readiness" "El camarero con caja" "$work/cashier-agent.log"

(
  cd "$repo_root/apps/bff"
  exec env -u SEATING_MCP_URL \
    UV_PROJECT_ENVIRONMENT="$work/bff-venv" \
    WAITER_AGENT_URL="http://127.0.0.1:$cashier_agent_port" \
    BFF_DATABASE_PATH="$work/cashier-bff.db" \
    BFF_SERVE_DELAY_SECONDS=1 \
    uv run --frozen uvicorn bff.main:create_app --factory \
      --host 127.0.0.1 --port "$cashier_bff_port" --workers 1
) >"$work/cashier-bff.log" 2>&1 &
pids+=("$!")
wait_for "http://127.0.0.1:$cashier_bff_port/healthz" "El BFF con caja" "$work/cashier-bff.log"

cd "$repo_root/apps/frontend"
if ! env -u SEATING_MCP_URL E2E_BFF_URL="http://127.0.0.1:$bff_port" \
  E2E_CASHIER_BFF_URL="http://127.0.0.1:$cashier_bff_port" \
  UV_PROJECT_ENVIRONMENT="$work/frontend-venv" \
  uv run --frozen pytest -p no:cacheprovider "$repo_root/tests/end_to_end" "$@"; then
  show_logs
  exit 1
fi
