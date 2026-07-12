#!/usr/bin/env bash
set -euo pipefail
echo "[deprecated] scripts/status.sh → use: hca ps / hca watch" >&2
if command -v hca >/dev/null 2>&1; then
  exec hca ps "$@"
fi
exit 1
