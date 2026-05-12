#!/usr/bin/env bash
set -euo pipefail

BOARD="hca-smoke-$$"
DRY_RUN=false
KEEP=false
usage(){
  cat <<'USAGE'
Usage: smoke-kanban-flow.sh [OPTIONS]

Creates an isolated Hermes kanban board and validates core coordination:
board create, parent/child dependency, claim, completion promotion,
block/unblock, and stale-claim reclaim.

Options:
  --board NAME   Board slug (default: hca-smoke-<pid>)
  --dry-run      Print intended commands only
  --keep         Do not archive the temporary board
  -h, --help     Show this help
USAGE
}
run(){
  if [[ "$DRY_RUN" == true ]]; then printf '[dry-run] %q ' "$@"; echo; else "$@"; fi
}
json_id(){ python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'; }
assert_show_contains(){
  local task_id="$1" expected="$2"
  if ! hermes kanban --board "$BOARD" show "$task_id" | grep -q "$expected"; then
    echo "Expected task $task_id to contain: $expected" >&2
    hermes kanban --board "$BOARD" show "$task_id" >&2 || true
    exit 1
  fi
}
cleanup(){
  if [[ "$DRY_RUN" == false && "$KEEP" == false ]]; then
    hermes kanban boards rm "$BOARD" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --board) BOARD="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --keep) KEEP=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if ! command -v hermes >/dev/null 2>&1; then echo "hermes not found" >&2; exit 1; fi
if ! command -v python3 >/dev/null 2>&1; then echo "python3 not found" >&2; exit 1; fi

echo "[smoke] board=$BOARD dry_run=$DRY_RUN"
if [[ "$DRY_RUN" == true ]]; then
  run hermes kanban boards create "$BOARD"
  run hermes kanban --board "$BOARD" create "HCA smoke parent" --assignee orchestrator --json
  run hermes kanban --board "$BOARD" create "HCA smoke child" --assignee coder-worker --parent '<parent-id>' --json
  run hermes kanban --board "$BOARD" claim '<parent-id>'
  run hermes kanban --board "$BOARD" complete '<parent-id>' --summary 'parent done'
  run hermes kanban --board "$BOARD" claim '<child-id>'
  run hermes kanban --board "$BOARD" block '<child-id>' 'human input test'
  run hermes kanban --board "$BOARD" unblock '<child-id>'
  run hermes kanban --board "$BOARD" reclaim '<child-id>'
  echo "Kanban smoke script PASS (dry-run)"
  exit 0
fi

hermes kanban boards create "$BOARD" >/dev/null
PARENT_JSON=$(hermes kanban --board "$BOARD" create "HCA smoke parent" --assignee orchestrator --json)
PARENT_ID=$(json_id <<< "$PARENT_JSON")
CHILD_JSON=$(hermes kanban --board "$BOARD" create "HCA smoke child" --assignee coder-worker --parent "$PARENT_ID" --json)
CHILD_ID=$(json_id <<< "$CHILD_JSON")

assert_show_contains "$CHILD_ID" "status:    todo"
hermes kanban --board "$BOARD" claim "$PARENT_ID" >/dev/null
hermes kanban --board "$BOARD" complete "$PARENT_ID" --summary 'parent done' >/dev/null
assert_show_contains "$CHILD_ID" "status:    ready"
hermes kanban --board "$BOARD" claim "$CHILD_ID" >/dev/null
assert_show_contains "$CHILD_ID" "status:    running"
hermes kanban --board "$BOARD" block "$CHILD_ID" 'human input test' >/dev/null
assert_show_contains "$CHILD_ID" "status:    blocked"
hermes kanban --board "$BOARD" unblock "$CHILD_ID" >/dev/null
assert_show_contains "$CHILD_ID" "status:    ready"
hermes kanban --board "$BOARD" claim "$CHILD_ID" >/dev/null
hermes kanban --board "$BOARD" reclaim "$CHILD_ID" >/dev/null
assert_show_contains "$CHILD_ID" "status:    ready"

echo "Kanban smoke script PASS: parent=$PARENT_ID child=$CHILD_ID board=$BOARD"
