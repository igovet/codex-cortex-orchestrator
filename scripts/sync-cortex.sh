#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
case "${1:-}" in
  --check|--dry-run)
    python3 -B "${script_dir}/validate-cortex-marketplace.py"
    ;;
  '')
    # Only cortex-dev establishes this exact isolated boundary.
    test -n "${CORTEX_DEV_OWNER_HOME:-}" || { echo 'Use scripts/cortex-dev for isolated preparation.' >&2; exit 2; }
    python3 -B "${script_dir}/cortex_package.py" prepare
    ;;
  *) echo 'Usage: scripts/sync-cortex.sh [--check|--dry-run]' >&2; exit 2;;
esac
