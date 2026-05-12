#!/usr/bin/env bash
set -euo pipefail

# hermes-concurrent-agents setup script
# Creates isolated worker profiles, copies SOUL.md templates, initializes kanban board

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILES_DIR="$SCRIPT_DIR/profiles"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[info]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
err()   { echo -e "${RED}[error]${NC} $*" >&2; }

echo ""
echo "=========================================="
echo "  hermes-concurrent-agents setup"
echo "  by @mr-r0b0t — r0b0tlab"
echo "=========================================="
echo ""

# --- Prerequisites ---
info "Checking prerequisites..."

if ! command -v hermes &>/dev/null; then
    err "hermes not found. Install first:"
    echo "  curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash"
    exit 1
fi
ok "hermes found: $(hermes --version 2>/dev/null || echo 'installed')"

if ! command -v tmux &>/dev/null; then
    err "tmux not found. Install with: apt install tmux / brew install tmux"
    exit 1
fi
ok "tmux found: $(tmux -V)"

# --- Create Profiles ---
WORKER_PROFILES=("creative-worker" "coder-worker" "research-worker" "qa-worker" "orchestrator")

echo ""
info "Creating worker profiles..."

for profile in "${WORKER_PROFILES[@]}"; do
    if [ -d "$HOME/.hermes/profiles/$profile" ]; then
        warn "Profile '$profile' already exists, skipping creation"
    else
        info "Creating profile: $profile"
        hermes profile create "$profile" --clone --no-alias 2>/dev/null || {
            warn "Failed to create '$profile' with --clone, trying without"
            hermes profile create "$profile" --no-alias 2>/dev/null || {
                err "Failed to create profile '$profile'"
                continue
            }
        }
        ok "Created profile: $profile"
    fi

    # Copy SOUL.md template
    SOUL_SRC="$PROFILES_DIR/$profile/SOUL.md"
    SOUL_DST="$HOME/.hermes/profiles/$profile/SOUL.md"
    if [ -f "$SOUL_SRC" ]; then
        cp "$SOUL_SRC" "$SOUL_DST"
        ok "Copied SOUL.md for $profile"
    else
        warn "No SOUL.md template found for $profile at $SOUL_SRC"
    fi

    # Apply config template
    CONFIG_SRC="$PROFILES_DIR/../config/profile-template.yaml"
    CONFIG_DST="$HOME/.hermes/profiles/$profile/config.yaml"
    if [ -f "$CONFIG_SRC" ]; then
        cp "$CONFIG_SRC" "$CONFIG_DST"
        ok "Applied config template for $profile"
    fi
done

# --- Initialize Kanban ---
echo ""
info "Initializing kanban board..."
hermes kanban init 2>/dev/null || warn "Kanban already initialized"
ok "Kanban board ready"

# --- Create wrapper scripts directory ---
WRAPPER_DIR="$HOME/.local/bin"
mkdir -p "$WRAPPER_DIR"

# --- Summary ---
echo ""
echo "=========================================="
echo "  Setup complete!"
echo "=========================================="
echo ""
echo "Profiles created:"
for profile in "${WORKER_PROFILES[@]}"; do
    if [ -d "$HOME/.hermes/profiles/$profile" ]; then
        echo "  ✓ $profile"
    else
        echo "  ✗ $profile (failed)"
    fi
done

echo ""
echo "Next steps:"
echo "  1. Start your inference backend (SGLang/vLLM/Ollama)"
echo "     docker compose -f config/sglang/docker-compose.yml up -d"
echo ""
echo "  2. Configure each profile's model (if different from default):"
echo "     hermes -p creative-worker model"
echo ""
echo "  3. Spawn workers:"
echo "     bash scripts/spawn.sh 3"
echo ""
echo "  4. Create tasks:"
echo "     hermes kanban create 'Your task' --assignee creative-worker"
echo ""
echo "  5. Start the dispatcher:"
echo "     hermes gateway start"
echo ""
echo "  6. Monitor:"
echo "     bash scripts/status.sh"
echo ""