#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROFILES_DIR="$ROOT/profiles"
CONFIG_SRC="$ROOT/config/mm27/profile-template.yaml"

ENDPOINT="${HCA_ENDPOINT:-http://127.0.0.1:8000/v1}"
MODEL="${HCA_MODEL_NAME:-minimax-m27-nvfp4}"
PROVIDER="${HCA_PROVIDER_NAME:-local-mm27-vllm}"
PROFILE_PREFIX="${HCA_PROFILE_PREFIX:-mm27}"
FORCE=false
DRY_RUN=false

usage(){
  cat <<'USAGE'
Usage: setup-mm27-demo.sh [OPTIONS]

Create/update Hermes profiles for a fully local MiniMax M2.7 NVFP4 team demo.
This setup deliberately targets the FlashInfer-CUTLASS MM2.7 path.

Options:
  --endpoint URL       OpenAI-compatible base URL (default: http://127.0.0.1:8000/v1)
  --model NAME         Served model name (default: minimax-m27-nvfp4)
  --provider NAME      Provider key for profile configs (default: local-mm27-vllm)
  --prefix NAME        Profile prefix (default: mm27)
  --force              Replace existing config.yaml after timestamped backup
  --dry-run            Print actions without changing profiles
  -h, --help           Show help

Created profiles:
  <prefix>-orchestrator, <prefix>-coder, <prefix>-research,
  <prefix>-creative, <prefix>-qa
USAGE
}

log(){ printf '[%s] %s\n' "$1" "$2"; }
run(){ if [[ "$DRY_RUN" == true ]]; then printf '[dry-run] %q ' "$@"; echo; else "$@"; fi; }
render_config(){
  local dst="$1"
  sed \
    -e "s#__MODEL_NAME__#${MODEL//\/\\}#g" \
    -e "s#__ENDPOINT__#${ENDPOINT//\/\\}#g" \
    -e "s#__PROVIDER_NAME__#${PROVIDER//\/\\}#g" \
    "$CONFIG_SRC" > "$dst"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --endpoint) ENDPOINT="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --provider) PROVIDER="$2"; shift 2 ;;
    --prefix) PROFILE_PREFIX="$2"; shift 2 ;;
    --force) FORCE=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "$DRY_RUN" != true ]]; then
  command -v hermes >/dev/null || { echo "[error] hermes not found" >&2; exit 2; }
  [[ -f "$CONFIG_SRC" ]] || { echo "[error] missing $CONFIG_SRC" >&2; exit 2; }
fi

roles=(orchestrator coder research creative qa)
for role in "${roles[@]}"; do
  profile="${PROFILE_PREFIX}-${role}"
  profile_dir="$HOME/.hermes/profiles/$profile"
  if [[ ! -d "$profile_dir" ]]; then
    log info "creating profile $profile"
    run hermes profile create "$profile" --clone --no-alias || run hermes profile create "$profile" --no-alias
  else
    log warn "profile exists: $profile"
  fi

  run mkdir -p "$profile_dir"
  soul_src="$PROFILES_DIR/$profile/SOUL.md"
  generic_soul_src="$PROFILES_DIR/${role}-worker/SOUL.md"
  [[ "$role" == orchestrator ]] && generic_soul_src="$PROFILES_DIR/orchestrator/SOUL.md"
  if [[ -f "$soul_src" ]]; then
    run cp "$soul_src" "$profile_dir/SOUL.md"
  elif [[ -f "$generic_soul_src" ]]; then
    run cp "$generic_soul_src" "$profile_dir/SOUL.md"
  fi

  cfg="$profile_dir/config.yaml"
  if [[ -f "$cfg" && "$FORCE" != true ]]; then
    log warn "preserving existing config for $profile (use --force to replace)"
  else
    if [[ -f "$cfg" ]]; then
      backup="$cfg.bak.$(date -u +%Y%m%dT%H%M%SZ)"
      run cp "$cfg" "$backup"
      log info "backed up $cfg to $backup"
    fi
    if [[ "$DRY_RUN" == true ]]; then
      echo "[dry-run] render $CONFIG_SRC -> $cfg model=$MODEL endpoint=$ENDPOINT provider=$PROVIDER"
    else
      tmp=$(mktemp)
      render_config "$tmp"
      mv "$tmp" "$cfg"
    fi
  fi
done

run hermes kanban init || true

echo "MM2.7 demo profiles ready: ${roles[*]/#/${PROFILE_PREFIX}-}"
echo "Endpoint: $ENDPOINT"
echo "Model:    $MODEL"
echo "Verify:   bash scripts/check-backend.sh --endpoint '$ENDPOINT' --model '$MODEL'"
echo "Local:    bash scripts/verify-local-only.sh --profiles '${PROFILE_PREFIX}-orchestrator,${PROFILE_PREFIX}-coder,${PROFILE_PREFIX}-research,${PROFILE_PREFIX}-creative,${PROFILE_PREFIX}-qa' --endpoint '$ENDPOINT' --provider '$PROVIDER' --model '$MODEL'"
