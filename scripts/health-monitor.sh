#!/usr/bin/env bash
set -euo pipefail

# Health monitor — watches GPU, memory, disk, and worker sessions
# Alerts when thresholds are exceeded

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

INTERVAL=30
GPU_THRESH=85
MEM_THRESH=90
DISK_THRESH=85
PREFIX="hca"
ONCE=false
FAIL_ON_ALERT=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --interval) INTERVAL="$2"; shift 2 ;;
        --gpu-threshold) GPU_THRESH="$2"; shift 2 ;;
        --mem-threshold) MEM_THRESH="$2"; shift 2 ;;
        --disk-threshold) DISK_THRESH="$2"; shift 2 ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        --once) ONCE=true; shift ;;
        --fail-on-alert) FAIL_ON_ALERT=true; shift ;;
        -h|--help)
            echo "Usage: $(basename "$0") [OPTIONS]"
            echo "  --interval SEC       Check interval (default: 30)"
            echo "  --gpu-threshold PCT  GPU memory alert threshold (default: 85)"
            echo "  --mem-threshold PCT  System memory alert threshold (default: 90)"
            echo "  --disk-threshold PCT Disk usage alert threshold (default: 85)"
            echo "  --prefix PREFIX      Worker tmux prefix (default: hca)"
            echo "  --once               Run one check and exit"
            echo "  --fail-on-alert      Exit non-zero when alerts are present"
            exit 0
            ;;
        *) shift ;;
    esac
done

echo -e "${BLUE}[health-monitor]${NC} Watching every ${INTERVAL}s (Ctrl-C to stop)"
echo -e "  Thresholds: GPU=${GPU_THRESH}% MEM=${MEM_THRESH}% DISK=${DISK_THRESH}%"
echo ""

while true; do
    ALERTS=()
    TIMESTAMP=$(date '+%H:%M:%S')

    # GPU memory
    if command -v nvidia-smi &>/dev/null; then
        GPU_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -cd '0-9')
        GPU_TOTAL=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -cd '0-9')
        if [ -n "$GPU_USED" ] && [ -n "$GPU_TOTAL" ] && [ "$GPU_TOTAL" -gt 0 ]; then
            GPU_PCT=$((GPU_USED * 100 / GPU_TOTAL))
            if [ "$GPU_PCT" -ge "$GPU_THRESH" ]; then
                ALERTS+=("GPU memory: ${GPU_PCT}% (${GPU_USED}/${GPU_TOTAL} MB)")
            fi
        fi
    fi

    # System memory
    MEM_PCT=$(free | awk 'NR==2{printf "%.0f", $3*100/$2}')
    if [ "$MEM_PCT" -ge "$MEM_THRESH" ]; then
        ALERTS+=("System memory: ${MEM_PCT}%")
    fi

    # Disk
    DISK_PCT=$(df / | awk 'NR==2{gsub(/%/,""); print $5}')
    if [ "$DISK_PCT" -ge "$DISK_THRESH" ]; then
        ALERTS+=("Disk usage: ${DISK_PCT}%")
    fi

    # Worker sessions
    SESSIONS=$(tmux list-sessions 2>/dev/null | grep "^${PREFIX}-" || true)
    SESSION_COUNT=$(echo "$SESSIONS" | grep -c . || echo 0)
    if [ "$SESSION_COUNT" -eq 0 ] && [ -n "$SESSIONS" ]; then
        SESSION_COUNT=0
    fi

    # Output
    if [ ${#ALERTS[@]} -gt 0 ]; then
        echo -e "${RED}[${TIMESTAMP}] ALERTS:${NC}"
        for alert in "${ALERTS[@]}"; do
            echo -e "  ${RED}⚠${NC} $alert"
        done
    else
        echo -e "${GREEN}[${TIMESTAMP}]${NC} healthy | workers: ${SESSION_COUNT} | mem: ${MEM_PCT}% | disk: ${DISK_PCT}%"
    fi

    if [ "$ONCE" = true ]; then
        if [ ${#ALERTS[@]} -gt 0 ] && [ "$FAIL_ON_ALERT" = true ]; then
            exit 2
        fi
        exit 0
    fi

    sleep "$INTERVAL"
done
