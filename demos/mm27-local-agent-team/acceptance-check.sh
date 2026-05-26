#!/usr/bin/env bash
set -euo pipefail

PROJECT="${1:-}"
[[ -n "$PROJECT" ]] || { echo "Usage: acceptance-check.sh PROJECT_DIR" >&2; exit 2; }
[[ -d "$PROJECT" ]] || { echo "[fail] missing project dir: $PROJECT" >&2; exit 1; }

fail=0
check_file(){
  local path="$1"
  if [[ -f "$PROJECT/$path" ]]; then
    echo "[pass] file exists: $path"
  else
    echo "[fail] missing file: $path" >&2
    fail=1
  fi
}
check_contains(){
  local path="$1" pattern="$2" desc="$3"
  if grep -Eiq "$pattern" "$PROJECT/$path" 2>/dev/null; then
    echo "[pass] $desc"
  else
    echo "[fail] $desc" >&2
    fail=1
  fi
}

check_file SPEC.md
check_file data/sample_run.jsonl
check_file src/build_dashboard.py
check_file public/index.html
check_file DEMO_CAPTION.md
check_file REPORT.md

if [[ -f "$PROJECT/src/build_dashboard.py" && -f "$PROJECT/data/sample_run.jsonl" ]]; then
  (cd "$PROJECT" && python3 src/build_dashboard.py data/sample_run.jsonl public/index.html) || fail=1
fi

if [[ -f "$PROJECT/tests/test_build_dashboard.py" ]]; then
  (cd "$PROJECT" && python3 tests/test_build_dashboard.py) || fail=1
else
  echo "[warn] tests/test_build_dashboard.py absent; checking generated artifact directly"
fi

if [[ -f "$PROJECT/public/index.html" ]]; then
  check_contains public/index.html 'Local Agent Team' 'dashboard title mentions Local Agent Team'
  check_contains public/index.html '#00ff88' 'dashboard includes r0b0tlab green'
  check_contains public/index.html '#ff00e5' 'dashboard includes r0b0tlab magenta'
  check_contains public/index.html '#00e5ff' 'dashboard includes r0b0tlab cyan'
  if grep -Eiq 'https?://|cdn\.|unpkg\.com|jsdelivr\.net|googleapis\.com' "$PROJECT/public/index.html"; then
    echo "[fail] dashboard contains external network dependency" >&2
    fail=1
  else
    echo "[pass] dashboard has no obvious external network dependency"
  fi
fi

if [[ -f "$PROJECT/REPORT.md" ]]; then
  check_contains REPORT.md 'QA.*PASS|PASS.*QA|verdict.*PASS' 'REPORT.md records QA PASS verdict'
  check_contains REPORT.md 'accept|reject|rework|review' 'REPORT.md describes orchestrator review/accept/rework loop'
fi

exit "$fail"
