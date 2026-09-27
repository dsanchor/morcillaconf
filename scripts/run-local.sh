#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
agent_root="$repo_root/agents/restaurant"
agent_dir="$agent_root/src/restaurant_agent"

cd "$agent_root"
export VIRTUAL_ENV="$agent_dir/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
AZURE_DEV_USER_AGENT=microsoft_foundry_skill \
  azd ai agent run restaurant --no-client "$@"
