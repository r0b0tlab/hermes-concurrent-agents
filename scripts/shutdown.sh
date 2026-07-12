#!/usr/bin/env bash
set -euo pipefail
echo "[deprecated] scripts/shutdown.sh → use: hca up stopped via Ctrl-C / drain (down landing next)" >&2
if command -v hca >/dev/null 2>&1; then
  echo "Tip: stop supervisor with Ctrl-C; kill slots via tmux -L <socket> list-sessions" >&2
fi
exit 0
