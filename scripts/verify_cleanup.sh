#!/usr/bin/env bash
# Residual-resource check (read-only). Exits non-zero if anything of this project is left.
# Usage: scripts/verify_cleanup.sh [--params FILE] [--regions R1 R2 ...] [--bootstrap-keys FILE]
set -uo pipefail
cd "$(dirname "$0")/.."
exec uv run python scripts/verify_cleanup.py "$@"
