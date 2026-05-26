#!/usr/bin/env bash
set -euo pipefail

SESSION="mm27-demo"
WORKSPACE="${HCA_DEMO_WORKSPACE:-$PWD/demo-workspace}"
PROFILE_PREFIX="${HCA_PROFILE_PREFIX:-mm27}"
DRY_RUN=false
NO_BRIEFING=false

usage(){
  cat <<'USAGE'
Usage: spawn-mm27-demo.sh [OPTIONS]

Create an OBS-friendly tmux layout for a fully local Hermes agent team demo.
The script starts five panes in one tmux session: orchestrator, coder, research,
creative, and QA. It does not kill unrelated sessions.

Options:
  --session NAME      tmux session name (default: mm27-demo)
  --workspace DIR     demo workspace passed in briefing
  --prefix NAME       Hermes profile prefix (default: mm27)
  --no-briefing       Start profiles only; do not send kickoff briefing
  --dry-run           Print tmux commands without running them
  -h, --help          Show help
USAGE
}

run(){ if [[ "$DRY_RUN" == true ]]; then printf '[dry-run] %q ' "$@"; echo; else "$@"; fi; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="$2"; shift 2 ;;
    --workspace) WORKSPACE="$2"; shift 2 ;;
    --prefix) PROFILE_PREFIX="$2"; shift 2 ;;
    --no-briefing) NO_BRIEFING=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

roles=(orchestrator coder research creative qa)
profiles=()
for role in "${roles[@]}"; do profiles+=("${PROFILE_PREFIX}-${role}"); done

if [[ "$DRY_RUN" != true ]]; then
  command -v tmux >/dev/null || { echo "[error] tmux not found" >&2; exit 2; }
  command -v hermes >/dev/null || { echo "[error] hermes not found" >&2; exit 2; }
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[error] tmux session already exists: $SESSION" >&2
    echo "        kill it explicitly with: tmux kill-session -t $SESSION" >&2
    exit 3
  fi
fi

run mkdir -p "$WORKSPACE"
run tmux new-session -d -s "$SESSION" -n agents -x 180 -y 55 "hermes -p ${profiles[0]} chat"
run tmux split-window -h -t "$SESSION:agents.0" "hermes -p ${profiles[1]} chat"
run tmux split-window -v -t "$SESSION:agents.0" "hermes -p ${profiles[2]} chat"
run tmux split-window -v -t "$SESSION:agents.1" "hermes -p ${profiles[3]} chat"
run tmux split-window -v -t "$SESSION:agents.3" "hermes -p ${profiles[4]} chat"
run tmux select-layout -t "$SESSION:agents" tiled

for i in "${!roles[@]}"; do
  title=$(printf '%s' "${roles[$i]}" | tr '[:lower:]' '[:upper:]')
  run tmux select-pane -t "$SESSION:agents.$i" -T "$title"
done

if [[ "$NO_BRIEFING" != true ]]; then
  briefing="You are part of a fully local Hermes Agent team running on a local OpenAI-compatible model endpoint. Workspace: $WORKSPACE. Coordinate through kanban. Save artifacts to disk. Do not use external APIs unless explicitly assigned. Your role is shown by your profile. Wait for the orchestrator mission or claim assigned kanban tasks."
  if [[ "$DRY_RUN" == true ]]; then
    echo "[dry-run] would send briefing to all panes: $briefing"
  else
    sleep 8
    for i in "${!roles[@]}"; do
      tmux send-keys -t "$SESSION:agents.$i" "$briefing" Enter
    done
  fi
fi

echo "tmux demo session ready: $SESSION"
echo "Attach for recording: tmux attach -t $SESSION"
echo "Workspace: $WORKSPACE"
