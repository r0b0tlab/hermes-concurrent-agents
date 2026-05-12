#!/usr/bin/env bash
set -euo pipefail

# hermes-concurrent-agents setup script
# Creates isolated worker profiles, copies SOUL.md templates, initializes kanban board.
# Safe by default: existing profile configs are preserved unless --force is passed.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILES_DIR="$SCRIPT_DIR/profiles"
CONFIG_SRC="$SCRIPT_DIR/config/profile-template.yaml"
DRY_RUN=false
FORCE=false

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info(){ echo -e "${BLUE}[info]${NC} $*"; }
ok(){ echo -e "${GREEN}[ok]${NC} $*"; }
warn(){ echo -e "${YELLOW}[warn]${NC} $*"; }
err(){ echo -e "${RED}[error]${NC} $*" >&2; }
usage(){
  cat <<'USAGE'
Usage: setup.sh [OPTIONS]

Options:
  --dry-run   Print actions without changing profiles or kanban
  --force     Overwrite existing profile config.yaml after making a timestamped backup
  -h, --help  Show this help

Safe default: existing ~/.hermes/profiles/<profile>/config.yaml files are preserved.
SOUL.md templates are updated because they are project role templates.
USAGE
}
run(){
  if [[ "$DRY_RUN" == true ]]; then printf '[dry-run] %q ' "$@"; echo; else "$@"; fi
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --force) FORCE=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "Unknown option: $1"; usage; exit 1 ;;
  esac
done

echo ""
echo "=========================================="
echo "  hermes-concurrent-agents setup"
echo "  by @mr-r0b0t — r0b0tlab"
echo "=========================================="
echo ""

info "Checking prerequisites..."
if ! command -v hermes >/dev/null 2>&1; then
  err "hermes not found. Install first: curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash"
  exit 1
fi
ok "hermes found: $(hermes --version 2>/dev/null || echo installed)"

if ! command -v tmux >/dev/null 2>&1; then
  err "tmux not found. Install with: apt install tmux / brew install tmux"
  exit 1
fi
ok "tmux found: $(tmux -V)"

if ! command -v python3 >/dev/null 2>&1; then
  err "python3 not found; scripts/benchmark.sh and docs validation require it"
  exit 1
fi
ok "python3 found"

if ! command -v curl >/dev/null 2>&1; then
  warn "curl not found; backend verification and real benchmarks will fail"
fi
if ! command -v bc >/dev/null 2>&1; then
  warn "bc not found; scripts/status.sh may show '?' for percentages"
fi

WORKER_PROFILES=("creative-worker" "coder-worker" "research-worker" "qa-worker" "orchestrator")

echo ""
info "Creating/updating worker profiles..."
for profile in "${WORKER_PROFILES[@]}"; do
  PROFILE_DIR="$HOME/.hermes/profiles/$profile"
  if [[ -d "$PROFILE_DIR" ]]; then
    warn "Profile '$profile' already exists"
  else
    info "Creating profile: $profile"
    run hermes profile create "$profile" --clone --no-alias || run hermes profile create "$profile" --no-alias
    ok "Created profile: $profile"
  fi

  SOUL_SRC="$PROFILES_DIR/$profile/SOUL.md"
  SOUL_DST="$PROFILE_DIR/SOUL.md"
  if [[ -f "$SOUL_SRC" ]]; then
    run mkdir -p "$PROFILE_DIR"
    run cp "$SOUL_SRC" "$SOUL_DST"
    ok "Applied SOUL.md for $profile"
  else
    warn "No SOUL.md template found for $profile at $SOUL_SRC"
  fi

  CONFIG_DST="$PROFILE_DIR/config.yaml"
  if [[ -f "$CONFIG_SRC" ]]; then
    if [[ -f "$CONFIG_DST" && "$FORCE" != true ]]; then
      warn "Preserving existing config for $profile. Use --force to replace after backup."
    else
      run mkdir -p "$PROFILE_DIR"
      if [[ -f "$CONFIG_DST" ]]; then
        BACKUP="$CONFIG_DST.bak.$(date -u +%Y%m%dT%H%M%SZ)"
        run cp "$CONFIG_DST" "$BACKUP"
        ok "Backed up $profile config to $BACKUP"
      fi
      run cp "$CONFIG_SRC" "$CONFIG_DST"
      ok "Applied config template for $profile"
    fi
  fi
done

echo ""
info "Initializing kanban board..."
run hermes kanban init || warn "Kanban may already be initialized"
ok "Kanban board ready"

echo ""
info "Backend verification hint:"
echo "  curl http://127.0.0.1:8000/v1/models"
echo "  bash scripts/benchmark.sh --dry-run --levels 1,2"
echo "  bash scripts/benchmark.sh --levels 1,2,3,4,6"

echo ""
echo "=========================================="
echo "  Setup complete"
echo "=========================================="
echo "Profiles: ${WORKER_PROFILES[*]}"
echo "Spawn:    bash scripts/spawn.sh 3"
echo "Monitor:  bash scripts/status.sh"
echo "Grade:    docs/grade/current-score.md"
echo ""
