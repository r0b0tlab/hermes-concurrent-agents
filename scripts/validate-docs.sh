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
for path in README.md SKILL.md LICENSE NOTICE SECURITY.md CHANGELOG.md CONTRIBUTING.md \
  setup.sh scripts/spawn.sh scripts/shutdown.sh scripts/status.sh \
  scripts/benchmark.sh scripts/health-monitor.sh scripts/smoke-kanban-flow.sh \
  scripts/check-backend.sh scripts/verify-local-only.sh \
  scripts/fault-injection-test.sh scripts/release-check.sh \
  scripts/public-safety-check.py scripts/generate-support-matrix.py \
  docs/deployment-guide.md docs/tuning-guide.md docs/workflow-patterns.md \
  docs/support-matrix.md docs/migration.md docs/security.md \
  docs/grade/rubric.md docs/grade/current-score.md \
  .github/workflows/ci.yml .github/workflows/hardware-benchmark.yml; do
  check_file "$path"
done

# Validate shell syntax.
for script in setup.sh scripts/*.sh; do
  bash -n "$script" || fail=1
done

# Parse workflow YAML for syntax. GitHub interprets a few YAML scalars with its
# own schema, so this is deliberately syntax-only rather than a fake Actions
# semantic validator.
"${PYTHON:-python3}" - <<'PY'
from pathlib import Path
import yaml
for path in sorted(Path('.github/workflows').glob('*.yml')):
    yaml.compose(path.read_text(encoding='utf-8'))
    print(f'[yaml-ok] {path}')
PY

# Verify every local Markdown link in the public Git candidate set. This includes
# new untracked docs while excluding ignored venv/build/vendor content.
"${PYTHON:-python3}" - <<'PY'
import re
import subprocess
import sys
from pathlib import Path

proc = subprocess.run(
    ["git", "ls-files", "-co", "--exclude-standard", "--", "*.md"],
    check=True,
    capture_output=True,
    text=True,
)
missing = []
for relative in proc.stdout.splitlines():
    path = Path(relative)
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
        target = match.group(1).split("#", 1)[0]
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        target_path = (path.parent / target).resolve()
        if not target_path.exists():
            missing.append((path, target))
if missing:
    for path, target in missing:
        print(f"[broken-link] {path}: {target}")
    sys.exit(1)
PY

if [[ "$fail" -ne 0 ]]; then
  echo "Validation failed"
  exit 1
fi

echo "Docs/scripts validation PASS"
