#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bff_dir="$repo_root/apps/bff"

cd "$bff_dir"
# The tests never call Foundry: they always use the scripted waiter.
BFF_WAITER=scripted uv run --frozen pytest tests "$@"
