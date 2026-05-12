#!/usr/bin/env bash
set -euo pipefail

# Non-destructive fault-injection harness. Default dry-run documents exactly what
# would be tested. Use --execute to actually spawn/kill prefix-scoped workers.

EXECUTE=false
PREFIX="hca-fault"
usage(){
  cat <<'USAGE'
Usage: fault-injection-test.sh [OPTIONS]

Options:
  --execute       Actually run prefix-scoped spawn/kill checks
  --prefix NAME   tmux session prefix (default: hca-fault)
  -h, --help      Show this help
USAGE
}
run(){
  if [[ "$EXECUTE" == true ]]; then "$@"; else printf '[dry-run] %q ' "$@"; echo; fi
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) EXECUTE=true; shift ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

echo "[fault] execute=$EXECUTE prefix=$PREFIX"
run bash scripts/benchmark.sh --dry-run --levels 1,2 --output-dir benchmarks/fault-dry-run
run bash scripts/smoke-kanban-flow.sh --dry-run --board "${PREFIX}-board"
run bash scripts/spawn.sh 1 --prefix "$PREFIX" --no-briefing
run tmux kill-session -t "${PREFIX}-1"
run bash scripts/spawn.sh 1 --prefix "$PREFIX" --no-briefing
run bash scripts/shutdown.sh "$PREFIX"
echo "Fault-injection harness PASS"
