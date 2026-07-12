#!/usr/bin/env bash
# Compatibility wrapper — prefer: hca up / hca watch
set -euo pipefail
echo "[deprecated] scripts/spawn.sh → use: hca init && hca up && hca watch" >&2
if command -v hca >/dev/null 2>&1; then
  exec hca up "$@"
fi
echo "Install package: pip install -e .  (from hermes-concurrent-agents root)" >&2
exit 1
