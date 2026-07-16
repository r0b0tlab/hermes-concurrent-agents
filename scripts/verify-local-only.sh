#!/usr/bin/env bash
set -euo pipefail

PROFILES="${HCA_PROFILES:-creative-worker,coder-worker,research-worker,qa-worker,orchestrator}"
ENDPOINT="${HCA_ENDPOINT:-http://127.0.0.1:8000/v1}"
PROVIDER="${HCA_PROVIDER_NAME:-custom}"
MODEL="${HCA_MODEL_NAME:-}"
DRY_RUN=false
SMOKE=false

usage(){
  cat <<'USAGE'
Usage: verify-local-only.sh [OPTIONS]

Verify Hermes worker profiles are configured for a local OpenAI-compatible
endpoint only. This is the guard for "fully local agent team" claims.

Options:
  --profiles CSV   Profiles to inspect
  --endpoint URL   Required local base URL
  --provider NAME  Required provider key/name
  --model NAME     Required served model name
  --smoke          Run a non-tool `hermes -p PROFILE -z ...` canary
  --dry-run        Print checks without reading profile files
  -h, --help       Show help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profiles) PROFILES="$2"; shift 2 ;;
    --endpoint) ENDPOINT="$2"; shift 2 ;;
    --provider) PROVIDER="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --smoke) SMOKE=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "$DRY_RUN" == true ]]; then
  echo "[dry-run] profiles=$PROFILES endpoint=$ENDPOINT provider=$PROVIDER model=${MODEL:-<not enforced>} smoke=$SMOKE"
  exit 0
fi

fail=0
IFS=',' read -r -a PROFILE_ARRAY <<< "$PROFILES"
for profile in "${PROFILE_ARRAY[@]}"; do
  cfg="$HOME/.hermes/profiles/$profile/config.yaml"
  echo "[check] $profile -> $cfg"
  if [[ ! -f "$cfg" ]]; then
    echo "[fail] missing config: $cfg" >&2
    fail=1
    continue
  fi
  if ! grep -q "provider: $PROVIDER" "$cfg"; then
    echo "[fail] $profile does not use provider: $PROVIDER" >&2
    fail=1
  fi
  if ! grep -q "base_url: $ENDPOINT" "$cfg"; then
    echo "[fail] $profile does not point to endpoint: $ENDPOINT" >&2
    fail=1
  fi
  if [[ -n "$MODEL" ]] && ! grep -q "default: $MODEL" "$cfg"; then
    echo "[fail] $profile does not use model: $MODEL" >&2
    fail=1
  fi
  if grep -Eiq 'openrouter|anthropic|nous|portal|api\.openai\.com|generativelanguage|x\.ai|groq|together|fireworks' "$cfg"; then
    echo "[fail] $profile config appears to reference a remote provider" >&2
    fail=1
  fi
  if [[ "$SMOKE" == true ]]; then
    hermes -p "$profile" -z 'Say LOCAL_PROFILE_READY and nothing else.' >/tmp/hca-profile-smoke.$$ 2>&1 || {
      echo "[fail] smoke failed for $profile" >&2
      cat /tmp/hca-profile-smoke.$$ >&2 || true
      fail=1
    }
    rm -f /tmp/hca-profile-smoke.$$
  fi
  echo "[ok] $profile local-only config check complete"
done

exit "$fail"
