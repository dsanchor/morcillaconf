#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
agent_dir="$repo_root/agents/restaurant/src/restaurant_agent"

cd "$agent_dir"
uv run --frozen pytest
