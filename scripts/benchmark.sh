#!/usr/bin/env bash
set -euo pipefail

# Benchmark concurrent agent throughput at different concurrency levels
# Requires: inference backend running, hermes installed

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

BENCH_DIR="/tmp/hca-benchmark-$$"
RESULTS_FILE="benchmark-results.txt"
CONCURRENCY_LEVELS=(1 2 3 4 6)
PROMPT="Write a detailed 500-word analysis of the benefits and risks of autonomous AI agents in software development. Include specific examples."
PREFIX="bench"

cleanup() {
    echo ""
    info "Cleaning up benchmark sessions..."
    for level in "${CONCURRENCY_LEVELS[@]}"; do
        for ((i=1; i<=level; i++)); do
            tmux kill-session -t "${PREFIX}-${level}-${i}" 2>/dev/null || true
        done
    done
    rm -rf "$BENCH_DIR"
}

trap cleanup EXIT

info()  { echo -e "${BLUE}[info]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║      hermes-concurrent-agents — Benchmark       ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
echo ""

mkdir -p "$BENCH_DIR"

# Check prerequisites
if ! command -v hermes &>/dev/null; then
    echo -e "${RED}[error]${NC} hermes not found"
    exit 1
fi

info "Benchmark prompt: ${PROMPT:0:60}..."
info "Concurrency levels: ${CONCURRENCY_LEVELS[*]}"
echo ""

echo "============================================" | tee "$RESULTS_FILE"
echo "  hermes-concurrent-agents benchmark" | tee -a "$RESULTS_FILE"
echo "  $(date)" | tee -a "$RESULTS_FILE"
echo "============================================" | tee -a "$RESULTS_FILE"
echo "" | tee -a "$RESULTS_FILE"

printf "%-12s %-15s %-15s %-15s\n" "Concurrency" "Total Time (s)" "Est Total TPS" "Status" | tee -a "$RESULTS_FILE"
echo "------------------------------------------------------------" | tee -a "$RESULTS_FILE"

for level in "${CONCURRENCY_LEVELS[@]}"; do
    info "Testing concurrency: $level"

    START_TIME=$(date +%s)

    # Spawn N agents
    for ((i=1; i<=level; i++)); do
        SESS="${PREFIX}-${level}-${i}"
        tmux new-session -d -s "$SESS" -x 120 -y 30 "hermes chat -q '$PROMPT'" 2>/dev/null || {
            warn "Failed to spawn $SESS, trying alternate approach"
            tmux new-session -d -s "$SESS" -x 120 -y 30 "hermes -c" 2>/dev/null || true
        }
    done

    # Wait for all to complete (timeout: 5 minutes)
    TIMEOUT=300
    ELAPSED=0
    while [ $ELAPSED -lt $TIMEOUT ]; do
        ACTIVE=0
        for ((i=1; i<=level; i++)); do
            if tmux has-session -t "${PREFIX}-${level}-${i}" 2>/dev/null; then
                ACTIVE=$((ACTIVE + 1))
            fi
        done
        if [ $ACTIVE -eq 0 ]; then
            break
        fi
        sleep 5
        ELAPSED=$((ELAPSED + 5))
    done

    END_TIME=$(date +%s)
    TOTAL_TIME=$((END_TIME - START_TIME))

    # Estimate tokens (rough: ~800 output tokens for the prompt)
    EST_TOKENS=$((800 * level))
    if [ $TOTAL_TIME -gt 0 ]; then
        EST_TPS=$((EST_TOKENS / TOTAL_TIME))
    else
        EST_TPS=0
    fi

    if [ $ELAPSED -ge $TIMEOUT ]; then
        STATUS="timeout"
    else
        STATUS="ok"
    fi

    printf "%-12s %-15s %-15s %-15s\n" "$level" "$TOTAL_TIME" "$EST_TPS" "$STATUS" | tee -a "$RESULTS_FILE"

    # Cleanup between runs
    for ((i=1; i<=level; i++)); do
        tmux kill-session -t "${PREFIX}-${level}-${i}" 2>/dev/null || true
    done
    sleep 3
done

echo "" | tee -a "$RESULTS_FILE"
ok "Results saved to $RESULTS_FILE"
echo ""
echo "Note: These are rough estimates. For precise tok/s, check your"
echo "inference backend's metrics endpoint (SGLang: /metrics, vLLM: /stats)."
echo ""
