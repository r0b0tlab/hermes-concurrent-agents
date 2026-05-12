#!/usr/bin/env bash
set -euo pipefail

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PREFIX="${1:-hca}"

clear 2>/dev/null || true

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║      hermes-concurrent-agents — Dashboard       ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
echo ""

# --- GPU Status ---
echo -e "${BLUE}── GPU ──────────────────────────────────────────${NC}"
if command -v nvidia-smi &>/dev/null; then
    nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu \
        --format=csv,noheader,nounits 2>/dev/null | while IFS=',' read -r name used total util temp; do
        pct=$(echo "scale=0; $used * 100 / $total" | bc 2>/dev/null || echo "?")
        echo "  $name: ${used}/${total} MB (${pct}%) | GPU: ${util}% | Temp: ${temp}°C"
    done
else
    echo "  nvidia-smi not available"
fi

# --- Memory ---
echo ""
echo -e "${BLUE}── Memory ────────────────────────────────────────${NC}"
free -h 2>/dev/null | awk 'NR==2{printf "  Total: %s | Used: %s | Free: %s | Available: %s\n", $2, $3, $4, $7}'

# --- Disk ---
echo ""
echo -e "${BLUE}── Disk ──────────────────────────────────────────${NC}"
df -h / | awk 'NR==2{printf "  Total: %s | Used: %s (%s) | Free: %s\n", $2, $3, $5, $4}'

# --- tmux Sessions ---
echo ""
echo -e "${BLUE}── Worker Sessions ───────────────────────────────${NC}"
SESSIONS=$(tmux list-sessions 2>/dev/null | grep "^${PREFIX}-" || true)
if [ -n "$SESSIONS" ]; then
    echo "$SESSIONS" | while read -r line; do
        sess=$(echo "$line" | cut -d: -f1)
        echo -e "  ${GREEN}✓${NC} $sess"
    done
else
    echo "  No active workers (prefix: ${PREFIX}-)"
fi

# --- Kanban Board ---
echo ""
echo -e "${BLUE}── Kanban Board ──────────────────────────────────${NC}"
if command -v hermes &>/dev/null; then
    hermes kanban list 2>/dev/null | head -15 || echo "  No kanban board initialized"
else
    echo "  hermes not available"
fi

echo ""
