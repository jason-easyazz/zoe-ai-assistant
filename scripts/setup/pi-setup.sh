#!/usr/bin/env bash
# =============================================================================
# pi-setup.sh — Full Zoe Pi 5 deployment script
# Run on the Raspberry Pi as the 'pi' user.
# =============================================================================
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/jason-easyazz/zoe-ai-assistant/pi/scripts/setup/pi-setup.sh | bash
#   -- or --
#   bash scripts/setup/pi-setup.sh [--skip-wipe] [--skip-build] [--skip-models]
#
# Phases:
#   1. System prep (Docker upgrade, build tools, Python deps)
#   2. Build bitnet.cpp (ARM NEON, Clang 18+)
#   3. Build llama.cpp (Gemma server, :11435)
#   4. Download AI models (BitNet 2B-4T + Gemma 4 E2B)
#   5. Install MemPalace
#   6. Clone/update repo on pi branch
#   7. Install systemd units + start services
#   8. Start Docker services (docker compose)
#   9. Smoke test
# =============================================================================
set -euo pipefail

REPO_URL="https://github.com/jason-easyazz/zoe-ai-assistant.git"
REPO_BRANCH="pi"
REPO_DIR="$HOME/assistant"
MODELS_DIR="$HOME/models"
BITNET_DIR="$HOME/bitnet.cpp"
LLAMA_DIR="$HOME/llama.cpp"

SKIP_WIPE="${SKIP_WIPE:-false}"
SKIP_BUILD="${SKIP_BUILD:-false}"
SKIP_MODELS="${SKIP_MODELS:-false}"

for arg in "$@"; do
    case "$arg" in
        --skip-wipe)   SKIP_WIPE=true ;;
        --skip-build)  SKIP_BUILD=true ;;
        --skip-models) SKIP_MODELS=true ;;
    esac
done

log()  { echo -e "\n\033[1;32m>>> $*\033[0m"; }
warn() { echo -e "\033[1;33m[WARN] $*\033[0m"; }
fail() { echo -e "\033[1;31m[FAIL] $*\033[0m"; exit 1; }

# ── Phase 1: System prep ──────────────────────────────────────────────────────

log "Phase 1: System prep"

# Preserve pironman5 before touching systemd
PIRONMAN5_ENABLED=false
if systemctl is-enabled pironman5 2>/dev/null | grep -q enabled; then
    PIRONMAN5_ENABLED=true
    log "pironman5 detected — will preserve"
fi

# Stop old Zoe services (ignore errors — they may not exist)
log "Stopping old Zoe services..."
systemctl --user stop zoe-data hermes-agent openclaw-gateway bonsai-server llama-server 2>/dev/null || true
systemctl --user disable hermes-agent openclaw-gateway bonsai-server llama-server 2>/dev/null || true

if [[ "$SKIP_WIPE" != "true" ]]; then
    log "Removing old Zoe install (keeping pironman5, models directory, and .env)"
    # Remove old repo clones that aren't the target dir
    rm -rf "$HOME/zoe" "$HOME/zoe-home" "$HOME/zoe-assistant" 2>/dev/null || true
    # Clear old systemd units (NOT pironman5)
    for unit in hermes-agent.service openclaw-gateway.service bonsai-server.service llama-server.service zoe-data.service; do
        rm -f "$HOME/.config/systemd/user/$unit"
    done
    systemctl --user daemon-reload 2>/dev/null || true
fi

# Upgrade Docker if needed
DOCKER_VERSION=$(docker --version 2>/dev/null | grep -oP '\d+\.\d+' | head -1 || echo "0.0")
DOCKER_MAJOR=$(echo "$DOCKER_VERSION" | cut -d. -f1)
if [[ "$DOCKER_MAJOR" -lt 26 ]]; then
    log "Upgrading Docker $DOCKER_VERSION → 26+"
    # Install Docker CE from official repo
    curl -fsSL https://get.docker.com | sudo bash
    sudo usermod -aG docker pi
    warn "Docker upgraded. If this is the first time, you may need to log out and back in for group membership."
else
    log "Docker $DOCKER_VERSION OK (>=26)"
fi

# Install build dependencies
log "Installing build dependencies..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    git curl wget cmake ninja-build \
    clang-18 llvm-18 \
    python3-pip python3-venv \
    libssl-dev libffi-dev \
    rsync jq

# Ensure Clang 18 is default
sudo update-alternatives --install /usr/bin/clang clang /usr/bin/clang-18 100 2>/dev/null || true
sudo update-alternatives --install /usr/bin/clang++ clang++ /usr/bin/clang++-18 100 2>/dev/null || true
clang --version || warn "Clang 18 not found — bitnet.cpp build may fail"

# ── Phase 2: Build bitnet.cpp ─────────────────────────────────────────────────

if [[ "$SKIP_BUILD" != "true" ]]; then
    log "Phase 2: Setting up BitNet b1.58 (ARM TL1, via setup_env.py)"
    if [[ ! -d "$BITNET_DIR" ]]; then
        git clone --depth 1 https://github.com/microsoft/BitNet.git "$BITNET_DIR"
    else
        cd "$BITNET_DIR" && git pull --rebase
    fi
    cd "$BITNET_DIR"
    # Install BitNet Python dependencies (includes huggingface_hub for model download)
    pip3 install --user -r requirements.txt --quiet
    # setup_env.py: builds bitnet.cpp kernel + downloads BitNet 2B-4T model
    # --quant-type tl1: ARM NEON lookup table (best for Pi 5 aarch64)
    # --use-pretuned: use Microsoft's pretuned kernel parameters for speed
    # Model is saved to: $BITNET_DIR/models/BitNet-b1.58-2B-4T/ggml-model-tl1.gguf
    python3 setup_env.py \
        --hf-repo microsoft/BitNet-b1.58-2B-4T \
        --quant-type tl1 \
        --use-pretuned
    log "BitNet setup done: $(ls $BITNET_DIR/models/BitNet-b1.58-2B-4T/ 2>/dev/null)"

    # ── Phase 3: Build standard llama.cpp (Gemma) ────────────────────────────

    log "Phase 3: Building llama.cpp (Gemma server)"
    if [[ ! -d "$LLAMA_DIR" ]]; then
        git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$LLAMA_DIR"
    else
        cd "$LLAMA_DIR" && git pull --rebase
    fi
    cd "$LLAMA_DIR"
    mkdir -p build && cd build
    cmake .. -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DLLAMA_NATIVE=ON
    ninja llama-server -j4
    log "llama.cpp built: $(ls $LLAMA_DIR/build/bin/llama-server)"
else
    log "Skipping builds (--skip-build)"
fi

# ── Phase 4: Download models ──────────────────────────────────────────────────

if [[ "$SKIP_MODELS" != "true" ]]; then
    log "Phase 4: Downloading Gemma 4 E2B model (BitNet model handled by setup_env.py above)"
    mkdir -p "$MODELS_DIR"
    pip3 install --user huggingface_hub --quiet

    # Gemma 4 E2B Q4_K_M (~3.46GB) from bartowski's imatrix GGUF collection
    # Same model as Jetson — fine-tuned weights can be shared between devices
    GEMMA_MODEL="$MODELS_DIR/google_gemma-4-E2B-it-Q4_K_M.gguf"
    if [[ ! -f "$GEMMA_MODEL" ]]; then
        log "Downloading Gemma 4 E2B Q4_K_M (~3.46GB)..."
        ~/.local/bin/huggingface-cli download \
            bartowski/google_gemma-4-E2B-it-GGUF \
            'google_gemma-4-E2B-it-Q4_K_M.gguf' \
            --local-dir "$MODELS_DIR"
    else
        log "Gemma model already present: $GEMMA_MODEL"
    fi
else
    log "Skipping model downloads (--skip-models)"
fi

# ── Phase 5: Install MemPalace ────────────────────────────────────────────────

log "Phase 5: Installing MemPalace"
pip3 install --user mempalace 2>/dev/null || pip3 install mempalace
mempalace init --data-dir "$HOME/.mempalace" 2>/dev/null || warn "mempalace init returned non-zero (may already be initialized)"

# Configure MemPalace Wings for Zoe domains
MEMPALACE_CFG="$HOME/.mempalace/config.yaml"
if [[ ! -f "$MEMPALACE_CFG" ]]; then
    mkdir -p "$HOME/.mempalace"
    cat > "$MEMPALACE_CFG" <<'MEMCFG'
wings:
  - name: personal
    description: User preferences, name, birthday, relationships
  - name: home
    description: Smart home devices, automations, routines
  - name: tasks
    description: Shopping lists, reminders, todos, calendar events
  - name: knowledge
    description: Learned facts, skills, how-to notes
MEMCFG
    log "MemPalace config created: $MEMPALACE_CFG"
fi

# ── Phase 6: Clone/update repo ────────────────────────────────────────────────

log "Phase 6: Cloning/updating repo on pi branch"
if [[ ! -d "$REPO_DIR/.git" ]]; then
    git clone --branch "$REPO_BRANCH" --depth 1 "$REPO_URL" "$REPO_DIR"
else
    cd "$REPO_DIR"
    git fetch origin
    git checkout "$REPO_BRANCH"
    git pull --rebase origin "$REPO_BRANCH"
fi

# Install Python deps for zoe-data
log "Installing zoe-data Python dependencies..."
cd "$REPO_DIR/services/zoe-data"
pip3 install --user -r requirements.txt

# ── Phase 7: Install systemd units ───────────────────────────────────────────

log "Phase 7: Installing systemd units"
bash "$REPO_DIR/scripts/setup/install-pi-systemd.sh"

# Start services in order
log "Starting inference servers..."
systemctl --user start bitnet-server
sleep 5  # Give BitNet 5s head start before Gemma (RAM budget)
systemctl --user start gemma-server mempalace

log "Waiting 15s for models to load..."
sleep 15

log "Starting zoe-data..."
systemctl --user start zoe-data

# ── Phase 8: Docker services ──────────────────────────────────────────────────

log "Phase 8: Starting Docker services"
cd "$REPO_DIR"
if [[ ! -f ".env" ]]; then
    warn ".env file not found. rsync it from Jetson first:"
    warn "  rsync zoe@<JETSON_IP>:/home/zoe/assistant/.env $REPO_DIR/.env"
    warn "Skipping docker compose for now."
else
    docker compose -f docker-compose.yml -f docker-compose.pi.yml up -d \
        zoe-ui zoe-auth homeassistant homeassistant-mcp-bridge \
        wyoming-piper wyoming-whisper wyoming-openwakeword \
        keeper
fi

# Restore pironman5 if it was active
if [[ "$PIRONMAN5_ENABLED" == "true" ]]; then
    systemctl enable pironman5 2>/dev/null || true
    systemctl start  pironman5 2>/dev/null || true
    log "pironman5 preserved and running"
fi

# ── Phase 9: Smoke tests ──────────────────────────────────────────────────────

log "Phase 9: Smoke tests"
sleep 5

PASS=0; FAIL=0

check() {
    local name="$1"; local cmd="$2"
    if eval "$cmd" &>/dev/null; then
        echo "  ✓ $name"
        PASS=$((PASS+1))
    else
        echo "  ✗ $name"
        FAIL=$((FAIL+1))
    fi
}

check "BitNet server health"   "curl -sf http://127.0.0.1:11434/v1/models"
check "Gemma server health"    "curl -sf http://127.0.0.1:11435/v1/models"
check "MemPalace data dir"     "test -d $HOME/.mempalace"
check "zoe-data health"        "curl -sf http://127.0.0.1:8000/health"
check "HA bridge health"       "curl -sf http://127.0.0.1:8007/"
check "nginx running"          "docker ps --format '{{.Names}}' | grep -q zoe-ui"
check "HA container running"   "docker ps --format '{{.Names}}' | grep -q homeassistant"

echo ""
echo "════════════════════════════════════════"
echo "Smoke tests: $PASS passed, $FAIL failed"
echo "════════════════════════════════════════"
if [[ "$FAIL" -gt 0 ]]; then
    warn "Some checks failed. Check logs:"
    warn "  systemctl --user status bitnet-server gemma-server zoe-data"
    warn "  docker compose -f docker-compose.yml -f docker-compose.pi.yml logs"
fi
echo ""
echo "Next steps:"
echo "  1. rsync Jetson ssl/ → $REPO_DIR/ssl/ (if not done)"
echo "  2. rsync Jetson data/zoe.db → $REPO_DIR/data/zoe.db"
echo "  3. rsync Jetson homeassistant/.storage/ → $REPO_DIR/homeassistant/.storage/"
echo "  4. In HA UI: Settings → Voice Assistants → enable 'Prefer local intents'"
echo "  5. Run integration tests: bash scripts/test/integration-tests.sh"
