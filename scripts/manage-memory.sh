#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
agent_dir="$repo_root/agents/restaurant/src/restaurant_agent"

if [[ -z "${MEMORY_DATABASE_PATH:-}" ]]; then
  printf 'MEMORY_DATABASE_PATH no está definida.\n' >&2
  printf 'Ejecuta primero: source ./.local/restaurant.env.sh\n' >&2
  exit 1
fi

cd "$agent_dir"
uv run --frozen python -m restaurant_agent.memory_admin "$@"
