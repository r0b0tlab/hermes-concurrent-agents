#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0
check_file(){
  local path="$1"
  if [[ ! -e "$path" ]]; then
    echo "[missing] $path"
    fail=1
  fi
}

# Referenced top-level files and scripts that must exist.
for path in README.md SKILL.md LICENSE CHANGELOG.md CONTRIBUTING.md \
  setup.sh scripts/spawn.sh scripts/shutdown.sh scripts/status.sh \
  scripts/benchmark.sh scripts/health-monitor.sh scripts/smoke-kanban-flow.sh \
  scripts/fault-injection-test.sh docs/deployment-guide.md docs/tuning-guide.md \
  docs/workflow-patterns.md docs/grade/rubric.md docs/grade/current-score.md; do
  check_file "$path"
done

# Validate shell syntax.
for script in setup.sh scripts/*.sh; do
  bash -n "$script" || fail=1
done

# Verify every local markdown link target exists (simple path links only).
python3 - <<'PY'
import os, re, sys
root='.'
missing=[]
for base, _, files in os.walk(root):
    if '.git' in base.split(os.sep):
        continue
    for name in files:
        if not name.endswith('.md'):
            continue
        path=os.path.join(base,name)
        text=open(path, encoding='utf-8').read()
        for m in re.finditer(r'\[[^\]]+\]\(([^)]+)\)', text):
            target=m.group(1).split('#',1)[0]
            if not target or '://' in target or target.startswith('mailto:'):
                continue
            target_path=os.path.normpath(os.path.join(os.path.dirname(path), target))
            if not os.path.exists(target_path):
                missing.append((path, target))
if missing:
    for path, target in missing:
        print(f'[broken-link] {path}: {target}')
    sys.exit(1)
PY

if [[ "$fail" -ne 0 ]]; then
  echo "Validation failed"
  exit 1
fi

echo "Docs/scripts validation PASS"
