#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[info]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }

PREFIX="${1:-hca}"

echo ""
echo "=========================================="
echo "  Shutting down workers (prefix: ${PREFIX})"
echo "=========================================="
echo ""

SESSIONS=$(tmux list-sessions 2>/dev/null | grep "^${PREFIX}-" || true)

if [ -z "$SESSIONS" ]; then
    warn "No sessions found with prefix '${PREFIX}-'"
    exit 0
fi

# Graceful interrupt
info "Sending interrupt (Ctrl-C) to all workers..."
echo "$SESSIONS" | while read -r line; do
    sess=$(echo "$line" | cut -d: -f1)
    tmux send-keys -t "$sess" C-c 2>/dev/null || true
done

sleep 5

# Kill sessions
info "Killing sessions..."
echo "$SESSIONS" | while read -r line; do
    sess=$(echo "$line" | cut -d: -f1)
    if tmux kill-session -t "$sess" 2>/dev/null; then
        ok "Killed $sess"
    else
        warn "Already gone: $sess"
    fi
done

echo ""
ok "All workers shut down."
echo ""
