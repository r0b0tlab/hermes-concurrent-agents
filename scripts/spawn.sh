#!/usr/bin/env bash
set -euo pipefail

# Spawn N concurrent Hermes worker agents in tmux sessions

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[info]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
err()   { echo -e "${RED}[error]${NC} $*" >&2; }

usage() {
    echo "Usage: $(basename "$0") [OPTIONS] [NUM_WORKERS]"
    echo ""
    echo "Spawn concurrent Hermes worker agents in tmux sessions."
    echo ""
    echo "Arguments:"
    echo "  NUM_WORKERS     Number of workers to spawn (default: 3)"
    echo ""
    echo "Options:"
    echo "  --prefix PREFIX  Session name prefix (default: hca)"
    echo "  --profiles LIST  Comma-separated profile names"
    echo "  --no-briefing    Skip sending initial briefing to workers"
    echo "  --dry-run        Print actions without spawning or killing sessions"
    echo "  -h, --help       Show this help"
    echo ""
    echo "Examples:"
    echo "  $(basename "$0") 3"
    echo "  $(basename "$0") 4 --prefix agent"
    echo "  $(basename "$0") 2 --profiles creative-worker,coder-worker"
}

NUM_WORKERS=3
PREFIX="hca"
CUSTOM_PROFILES=""
NO_BRIEFING=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --prefix) PREFIX="$2"; shift 2 ;;
        --profiles) CUSTOM_PROFILES="$2"; shift 2 ;;
        --no-briefing) NO_BRIEFING=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) err "Unknown option: $1"; usage; exit 1 ;;
        *) NUM_WORKERS="$1"; shift ;;
    esac
done

# Default profile rotation
DEFAULT_PROFILES=("creative-worker" "coder-worker" "research-worker" "qa-worker" "orchestrator")

# Build profile list
if [ -n "$CUSTOM_PROFILES" ]; then
    IFS=',' read -ra PROFILES <<< "$CUSTOM_PROFILES"
else
    PROFILES=()
    for ((i=0; i<NUM_WORKERS; i++)); do
        PROFILES+=("${DEFAULT_PROFILES[$((i % ${#DEFAULT_PROFILES[@]}))]}")
    done
fi

echo ""
echo "=========================================="
echo "  Spawning $NUM_WORKERS concurrent agents"
echo "=========================================="
echo ""

# Kill existing sessions with this prefix
EXISTING=$(tmux list-sessions 2>/dev/null | grep "^${PREFIX}-" || true)
if [ -n "$EXISTING" ]; then
    warn "Killing existing sessions with prefix '${PREFIX}-':"
    echo "$EXISTING" | while read -r line; do
        sess=$(echo "$line" | cut -d: -f1)
        if [ "$DRY_RUN" = true ]; then
            info "  Would kill: $sess"
        else
            tmux kill-session -t "$sess" 2>/dev/null || true
            info "  Killed: $sess"
        fi
    done
fi

# Spawn workers
for ((i=0; i<NUM_WORKERS; i++)); do
    SESSION="${PREFIX}-$((i+1))"
    PROFILE="${PROFILES[$i]}"

    info "Spawning $SESSION with profile $PROFILE..."

    if [ "$DRY_RUN" = true ]; then
        ok "Would spawn $SESSION (profile: $PROFILE)"
        continue
    fi

    tmux new-session -d -s "$SESSION" -x 120 -y 50 \
        "hermes -p $PROFILE chat" 2>/dev/null || {
        err "Failed to spawn $SESSION"
        continue
    }

    # Readiness: session exists and pane is capturable. Prompt readiness is model/provider dependent,
    # so this is intentionally conservative and logs pane output on failure.
    READY=false
    for _ in {1..20}; do
        if tmux has-session -t "$SESSION" 2>/dev/null && tmux capture-pane -t "$SESSION" -p -S -5 >/dev/null 2>&1; then
            READY=true
            break
        fi
        sleep 1
    done
    if [ "$READY" != true ]; then
        warn "$SESSION did not become readable within 20s"
    fi

    ok "Spawned $SESSION (profile: $PROFILE)"
done

# Wait for startup (skipped in dry-run; actual readiness is checked per session)
if [ "$DRY_RUN" = false ]; then
    info "Workers are spawned; giving Hermes a short stabilization window..."
    sleep 2
fi

# Send briefing
if [ "$NO_BRIEFING" = false ] && [ "$DRY_RUN" = false ]; then
    echo ""
    info "Sending initial briefing to workers..."
    for ((i=0; i<NUM_WORKERS; i++)); do
        SESSION="${PREFIX}-$((i+1))"
        PROFILE="${PROFILES[$i]}"

        tmux send-keys -t "$SESSION" \
            "You are a worker agent (profile: $PROFILE). Claim and execute tasks from the kanban board. Use 'hermes kanban claim' or check for assigned tasks. Save progress to disk frequently. Report completion with kanban_complete." \
            Enter 2>/dev/null || warn "Could not send briefing to $SESSION"
    done
fi

# Summary
echo ""
echo "=========================================="
echo "  Workers spawned"
echo "=========================================="
echo ""
for ((i=0; i<NUM_WORKERS; i++)); do
    SESSION="${PREFIX}-$((i+1))"
    PROFILE="${PROFILES[$i]}"
    echo "  ✓ $SESSION → $PROFILE"
done
echo ""
echo "Monitor:   bash scripts/status.sh"
echo "Shutdown:  bash scripts/shutdown.sh"
echo "Benchmark: bash scripts/benchmark.sh"
echo ""
