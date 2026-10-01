#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bff_dir="$repo_root/apps/bff"

cd "$bff_dir"
# Tests inject their deterministic waiter directly; the runtime remains remote-only.
uv run --frozen pytest tests "$@"
