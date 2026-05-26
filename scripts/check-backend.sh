#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="${HCA_ENDPOINT:-http://127.0.0.1:8000/v1}"
MODEL="${HCA_MODEL_NAME:-}"
TIMEOUT=30
DRY_RUN=false

usage(){
  cat <<'USAGE'
Usage: check-backend.sh [OPTIONS]

Validate an OpenAI-compatible local backend before spawning Hermes workers.

Options:
  --endpoint URL   Base URL, e.g. http://127.0.0.1:8000/v1
  --model NAME     Served model name expected by chat/completions
  --timeout SEC    curl timeout for completion request (default: 30)
  --dry-run        Print checks without calling the backend
  -h, --help       Show help

Environment fallbacks:
  HCA_ENDPOINT
  HCA_MODEL_NAME
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --endpoint) ENDPOINT="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$MODEL" ]]; then
  echo "[error] --model or HCA_MODEL_NAME is required" >&2
  exit 2
fi

if [[ "$DRY_RUN" == true ]]; then
  echo "[dry-run] would GET $ENDPOINT/models"
  echo "[dry-run] would POST $ENDPOINT/chat/completions with model=$MODEL"
  exit 0
fi

command -v curl >/dev/null || { echo "[error] curl not found" >&2; exit 2; }
command -v python3 >/dev/null || { echo "[error] python3 not found" >&2; exit 2; }

models_json=$(curl -fsS --max-time 10 "$ENDPOINT/models")
printf '%s' "$models_json" | python3 - "$MODEL" <<'PY'
import json, sys
expected = sys.argv[1]
data = json.load(sys.stdin)
ids = [m.get('id', '') for m in data.get('data', [])]
if expected not in ids:
    print(f"[warn] expected model {expected!r} not present in /models ids={ids}")
else:
    print(f"[ok] /models includes {expected}")
PY

req=$(mktemp)
resp=$(mktemp)
trap 'rm -f "$req" "$resp"' EXIT
python3 - "$MODEL" > "$req" <<'PY'
import json, sys
print(json.dumps({
    "model": sys.argv[1],
    "messages": [{"role": "user", "content": "Say LOCAL_BACKEND_READY and nothing else."}],
    "temperature": 0,
    "max_tokens": 32
}))
PY

curl -fsS --max-time "$TIMEOUT" "$ENDPOINT/chat/completions" \
  -H 'Content-Type: application/json' \
  --data-binary "@$req" > "$resp"

python3 - "$resp" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
if 'error' in data:
    raise SystemExit(f"backend returned error: {data['error']}")
content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
usage = data.get('usage', {})
print(f"[ok] completion returned: {content.strip()[:120]}")
if usage:
    print(f"[ok] usage: {usage}")
PY
