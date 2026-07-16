#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mode="${1:---full}"
if [[ "$mode" != "--quick" && "$mode" != "--full" ]]; then
  echo "usage: scripts/release-check.sh [--quick|--full]" >&2
  exit 2
fi

if [[ -n "${PYTHON:-}" ]]; then
  python_bin="$PYTHON"
elif [[ -x .venv/bin/python ]]; then
  python_bin=.venv/bin/python
else
  python_bin=python3
fi

if [[ -n "${HCA_HERMES_SRC:-}" ]]; then
  if [[ ! -d "$HCA_HERMES_SRC/hermes_cli" ]]; then
    echo "HCA_HERMES_SRC does not contain hermes_cli: $HCA_HERMES_SRC" >&2
    exit 2
  fi
  export PYTHONPATH="$HCA_HERMES_SRC${PYTHONPATH:+:$PYTHONPATH}"
fi

echo "[release] static analysis"
"$python_bin" -m ruff check src tests scripts
"$python_bin" -m pyright
actionlint_bin="$("$python_bin" -c 'import sys; from pathlib import Path; print(Path(sys.executable).parent / "actionlint")')"
if [[ ! -x "$actionlint_bin" ]]; then
  echo "actionlint is missing; install the project development extra" >&2
  exit 2
fi
"$actionlint_bin" .github/workflows/*.yml

echo "[release] unit tests"
"$python_bin" -m pytest -q tests/unit

echo "[release] deterministic contract/release tests"
"$python_bin" -m pytest -q \
  tests/contract/test_compat_probes.py \
  tests/contract/test_release_workflows.py

echo "[release] generated support data"
"$python_bin" scripts/generate-support-matrix.py --check

echo "[release] docs and shell syntax"
PYTHON="$python_bin" bash scripts/validate-docs.sh

echo "[release] public source safety"
"$python_bin" scripts/public-safety-check.py

if [[ "$mode" == "--full" ]]; then
  echo "[release] complete test suite"
  "$python_bin" -m pytest -q
  echo "[release] wheel and source distribution"
  rm -rf -- build dist
  "$python_bin" -m build
fi

git diff --check
echo "release check PASS ($mode)"
