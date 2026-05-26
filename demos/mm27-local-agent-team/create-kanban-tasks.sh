#!/usr/bin/env bash
set -euo pipefail

BOARD="mm27-demo"
WORKSPACE=""
DRY_RUN=false

usage(){
  cat <<'USAGE'
Usage: create-kanban-tasks.sh --workspace DIR [OPTIONS]

Create a demo kanban dependency graph for one shared project:
local-agent-demo-dashboard. The orchestrator assigns/reviews/accepts/rejects,
and workers contribute requirements, implementation, copy, and QA evidence.

Options:
  --workspace DIR   Demo workspace root
  --board NAME      Kanban board name (default: mm27-demo)
  --dry-run         Print commands only
  -h, --help        Show help
USAGE
}

run(){ if [[ "$DRY_RUN" == true ]]; then printf '[dry-run] %q ' "$@"; echo; else "$@"; fi; }
json_id(){ python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace) WORKSPACE="$2"; shift 2 ;;
    --board) BOARD="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -n "$WORKSPACE" ]] || { echo "[error] --workspace is required" >&2; usage; exit 2; }
PROJECT="$WORKSPACE/project"

run mkdir -p "$PROJECT"
if [[ "$DRY_RUN" != true ]]; then
  cp "$(dirname "$0")/SPEC.md" "$PROJECT/SPEC.md"
fi

if [[ "$DRY_RUN" == true ]]; then
  run hermes kanban boards create "$BOARD"
  run hermes kanban --board "$BOARD" create "ORCH: plan local-agent-demo-dashboard and assign worker tasks" --assignee mm27-orchestrator --json
  run hermes kanban --board "$BOARD" create "RESEARCH: refine requirements and sample event schema" --assignee mm27-research --parent '<orch-id>' --json
  run hermes kanban --board "$BOARD" create "CODER: implement dashboard generator and tests" --assignee mm27-coder --parent '<research-id>' --json
  run hermes kanban --board "$BOARD" create "CREATIVE: write demo caption and dashboard copy" --assignee mm27-creative --parent '<research-id>' --json
  run hermes kanban --board "$BOARD" create "QA: run acceptance checker and report PASS/FAIL" --assignee mm27-qa --parent '<coder-id>' --json
  run hermes kanban --board "$BOARD" create "ORCH: review outputs, accept or reject for rework, then write REPORT.md" --assignee mm27-orchestrator --parent '<qa-id>' --json
  exit 0
fi

hermes kanban boards create "$BOARD" >/dev/null 2>&1 || true

ORCH=$(hermes kanban --board "$BOARD" create "ORCH: plan local-agent-demo-dashboard and assign worker tasks" --assignee mm27-orchestrator --json | json_id)
RESEARCH=$(hermes kanban --board "$BOARD" create "RESEARCH: refine requirements and sample event schema in $PROJECT" --assignee mm27-research --parent "$ORCH" --json | json_id)
CODER=$(hermes kanban --board "$BOARD" create "CODER: implement stdlib dashboard generator and tests in $PROJECT" --assignee mm27-coder --parent "$RESEARCH" --json | json_id)
CREATIVE=$(hermes kanban --board "$BOARD" create "CREATIVE: write polished dashboard copy and DEMO_CAPTION.md in $PROJECT" --assignee mm27-creative --parent "$RESEARCH" --json | json_id)
QA=$(hermes kanban --board "$BOARD" create "QA: run demos/mm27-local-agent-team/acceptance-check.sh $PROJECT and write QA evidence" --assignee mm27-qa --parent "$CODER" --json | json_id)
FINAL=$(hermes kanban --board "$BOARD" create "ORCH: review every artifact, accept or reject for rework, then write REPORT.md with final verdict" --assignee mm27-orchestrator --parent "$QA" --json | json_id)

cat > "$WORKSPACE/kanban-task-ids.env" <<EOF
BOARD=$BOARD
PROJECT=$PROJECT
ORCH_TASK=$ORCH
RESEARCH_TASK=$RESEARCH
CODER_TASK=$CODER
CREATIVE_TASK=$CREATIVE
QA_TASK=$QA
FINAL_TASK=$FINAL
EOF

printf 'Created demo board=%s project=%s\n' "$BOARD" "$PROJECT"
printf 'Task ids saved: %s\n' "$WORKSPACE/kanban-task-ids.env"
