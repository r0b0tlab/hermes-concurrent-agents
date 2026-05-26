#!/usr/bin/env bash
set -euo pipefail

BOARD="demo-optimized"
WORKSPACE=""
PREFIX="demo"
DRY_RUN=false

usage(){
  cat <<'USAGE'
Usage: create-optimized-demo-tasks.sh --workspace DIR [OPTIONS]

Create a low-wait dependency graph for the local-agent-demo-dashboard demo.
The graph fans out independent work immediately, then fans into integration,
QA, a mandatory rework/no-op gate, and final orchestrator acceptance.

Options:
  --workspace DIR   Demo workspace root
  --board NAME      Kanban board name (default: demo-optimized)
  --prefix NAME     Profile prefix (default: demo)
  --dry-run         Print commands only
  -h, --help        Show help

Expected profiles for --prefix demo:
  demo-orchestrator, demo-research, demo-coder, demo-creative, demo-qa
USAGE
}

run(){ if [[ "$DRY_RUN" == true ]]; then printf '[dry-run] %q ' "$@"; echo; else "$@"; fi; }
json_id(){ python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace) WORKSPACE="$2"; shift 2 ;;
    --board) BOARD="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -n "$WORKSPACE" ]] || { echo "[error] --workspace is required" >&2; usage; exit 2; }
PROJECT="$WORKSPACE/project"
ORCH="${PREFIX}-orchestrator"
RESEARCH="${PREFIX}-research"
CODER="${PREFIX}-coder"
CREATIVE="${PREFIX}-creative"
QA="${PREFIX}-qa"
TASK_WORKSPACE="dir:$PROJECT"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

create_task(){
  local title="$1" assignee="$2" body="$3" max_runtime="$4"; shift 4
  local args=(hermes kanban --board "$BOARD" create "$title" --assignee "$assignee" --workspace "$TASK_WORKSPACE" --max-runtime "$max_runtime" --json)
  local parent
  for parent in "$@"; do
    args+=(--parent "$parent")
  done
  if [[ "$DRY_RUN" == true ]]; then
    printf '[dry-run] %q ' "${args[@]}" --body "$body"
    echo
  else
    "${args[@]}" --body "$body" | json_id
  fi
}

run mkdir -p "$PROJECT"
if [[ "$DRY_RUN" != true ]]; then
  cp "$SCRIPT_DIR/SPEC.md" "$PROJECT/SPEC.md"
  cat > "$WORKSPACE/TRACE_EVIDENCE.md" <<EOF
# Trace Evidence Capture

Board: $BOARD
Workspace: $WORKSPACE
Project: $PROJECT

Trace artifacts to inspect during/after the run:
- Kanban events: hermes kanban --board $BOARD watch
- Task runs: hermes kanban --board $BOARD runs <task-id>
- Worker logs: hermes kanban --board $BOARD log <task-id>
- Task IDs: $WORKSPACE/optimized-kanban-task-ids.env
EOF
fi

if [[ "$DRY_RUN" == true ]]; then
  run hermes kanban boards create "$BOARD"
  create_task "T1 RESEARCH: requirements, event schema, acceptance criteria" "$RESEARCH" "No parents. Read $PROJECT/SPEC.md. Produce $PROJECT/SPEC_APPENDIX.md with event schema, acceptance criteria, and risks. Do not edit code." "20m"
  create_task "T2 CODER: scaffold generator, sample data, tests, HTML" "$CODER" "No parents. Build the stdlib-only dashboard slice in $PROJECT: data/sample_run.jsonl, src/build_dashboard.py, tests/test_build_dashboard.py, public/index.html. Run python3 tests/test_build_dashboard.py." "30m"
  create_task "T3 CREATIVE: dashboard copy and recording caption" "$CREATIVE" "No parents. Produce $PROJECT/DEMO_CAPTION.md and copy snippets suitable for the generated dashboard. Use r0b0tlab colors and @mr-r0b0t attribution where appropriate." "20m"
  create_task "T4 QA-PREP: checklist before implementation completes" "$QA" "No parents. Produce $PROJECT/QA_CHECKLIST.md listing exact acceptance commands, expected files, local-only checks, and trace-evidence checks. Do not sign off yet." "15m"
  create_task "T5 ORCH: integrate and review parallel outputs" "$ORCH" "Parents: T1-T4. Review artifacts, reconcile SPEC_APPENDIX/copy/code/QA checklist, request fixes via comments if needed, then write $PROJECT/INTEGRATION_REVIEW.md with accept/reject decisions." "25m" '<research-id>' '<coder-id>' '<creative-id>' '<qa-prep-id>'
  create_task "T6 QA: execute acceptance checker and capture evidence" "$QA" "Parent: T5. Run bash $REPO_ROOT/demos/mm27-local-agent-team/acceptance-check.sh $PROJECT and write $PROJECT/QA_EVIDENCE.md with command output and PASS/FAIL verdict." "20m" '<integration-id>'
  create_task "T7 CODER: rework gate or no-op proof" "$CODER" "Parent: T6. If QA failed, fix only failing items and rerun acceptance-check. If QA passed, write $PROJECT/REWORK_NOT_NEEDED.md citing QA_EVIDENCE.md. This gate must complete before final report." "25m" '<qa-run-id>'
  create_task "T8 ORCH: final report and recording-ready verdict" "$ORCH" "Parents: T6 and T7. Read QA_EVIDENCE.md and REWORK_NOT_NEEDED.md or rework evidence. Write $PROJECT/REPORT.md with worker contributions, accept/reject/rework loop, trace evidence locations, and final PASS only if QA passed." "20m" '<qa-run-id>' '<rework-gate-id>'
  exit 0
fi

hermes kanban boards create "$BOARD" >/dev/null 2>&1 || true

R=$(create_task "T1 RESEARCH: requirements, event schema, acceptance criteria" "$RESEARCH" "No parents. Read $PROJECT/SPEC.md. Produce $PROJECT/SPEC_APPENDIX.md with: JSONL event schema for agent trace data, acceptance criteria, project risks, and local-only boundary notes. Do not edit code." "20m")
C=$(create_task "T2 CODER: scaffold generator, sample data, tests, HTML" "$CODER" "No parents. Build the stdlib-only dashboard slice in $PROJECT: data/sample_run.jsonl, src/build_dashboard.py, tests/test_build_dashboard.py, public/index.html. Requirements: Local Agent Team title, #00ff88/#ff00e5/#00e5ff colors, table or cards summarizing worker contributions, no external network dependencies. Run python3 tests/test_build_dashboard.py and record output in a note or comment." "30m")
V=$(create_task "T3 CREATIVE: dashboard copy and recording caption" "$CREATIVE" "No parents. Produce $PROJECT/DEMO_CAPTION.md and dashboard copy snippets suitable for the recording. Include r0b0tlab visual language, concise social caption, and @mr-r0b0t attribution where appropriate. Do not depend on final code being complete." "20m")
Q0=$(create_task "T4 QA-PREP: checklist before implementation completes" "$QA" "No parents. Produce $PROJECT/QA_CHECKLIST.md listing exact acceptance commands, expected files, local-only checks, no-external-dependency checks, and trace-evidence checks. Do not sign off yet." "15m")
INT=$(create_task "T5 ORCH: integrate and review parallel outputs" "$ORCH" "Parents: T1-T4. Review SPEC_APPENDIX.md, code/test artifacts, DEMO_CAPTION.md, and QA_CHECKLIST.md. Reconcile conflicts, leave specific comments for any rejected work, and write $PROJECT/INTEGRATION_REVIEW.md with accept/reject decisions for each lane. Only complete once the project is ready for QA execution." "25m" "$R" "$C" "$V" "$Q0")
Q1=$(create_task "T6 QA: execute acceptance checker and capture evidence" "$QA" "Parent: T5. Run: bash $REPO_ROOT/demos/mm27-local-agent-team/acceptance-check.sh $PROJECT. Write $PROJECT/QA_EVIDENCE.md containing the exact command, output, PASS/FAIL verdict, and any remaining issues. Do not edit implementation files." "20m" "$INT")
RW=$(create_task "T7 CODER: rework gate or no-op proof" "$CODER" "Parent: T6. Read $PROJECT/QA_EVIDENCE.md. If QA failed, fix only failing items and rerun: bash $REPO_ROOT/demos/mm27-local-agent-team/acceptance-check.sh $PROJECT. If QA passed, write $PROJECT/REWORK_NOT_NEEDED.md citing QA_EVIDENCE.md. This mandatory gate prevents the final report from racing ahead of needed rework." "25m" "$Q1")
FINAL=$(create_task "T8 ORCH: final report and recording-ready verdict" "$ORCH" "Parents: T6 and T7. Read QA_EVIDENCE.md plus rework/no-op evidence. Write $PROJECT/REPORT.md with worker contributions, orchestrator accept/reject/rework decisions, QA evidence, trace evidence locations, and final PASS only if QA passed after any rework." "20m" "$Q1" "$RW")

cat > "$WORKSPACE/optimized-kanban-task-ids.env" <<EOF
BOARD=$BOARD
PROJECT=$PROJECT
TRACE_EVIDENCE=$WORKSPACE/TRACE_EVIDENCE.md
RESEARCH_TASK=$R
CODER_SCAFFOLD_TASK=$C
CREATIVE_TASK=$V
QA_PREP_TASK=$Q0
INTEGRATION_REVIEW_TASK=$INT
QA_RUN_TASK=$Q1
REWORK_GATE_TASK=$RW
FINAL_REPORT_TASK=$FINAL
EOF

printf 'Created optimized demo board=%s project=%s\n' "$BOARD" "$PROJECT"
printf 'Task ids saved: %s\n' "$WORKSPACE/optimized-kanban-task-ids.env"
printf 'Trace evidence guide: %s\n' "$WORKSPACE/TRACE_EVIDENCE.md"
