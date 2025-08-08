#!/bin/bash
# Zoe Installation/Upgrade Script
set -euo pipefail

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    exit 1
}

# Determine if sudo is needed
SUDO=""
if [ "$EUID" -ne 0 ]; then
    SUDO="sudo"
fi
DOCKER="${SUDO} docker"

REPO_URL="https://github.com/jason-easyazz/zoe-ai-assistant.git"
INSTALL_DIR="$HOME/zoe-ai-assistant"

check_cmd() {
    command -v "$1" >/dev/null 2>&1
}

log "🔍 Checking system requirements..."

# Ensure git
if ! check_cmd git; then
    log "Installing git..."
    $SUDO apt-get update -y
    $SUDO apt-get install -y git
fi

# Ensure curl
if ! check_cmd curl; then
    log "Installing curl..."
    $SUDO apt-get update -y
    $SUDO apt-get install -y curl
fi

# Ensure Docker
if ! check_cmd docker; then
    log "Installing Docker..."
    curl -fsSL https://get.docker.com | $SUDO sh
fi

# Ensure docker compose plugin
if ! $DOCKER compose version >/dev/null 2>&1; then
    log "Installing docker compose plugin..."
    $SUDO apt-get update -y
    $SUDO apt-get install -y docker-compose-plugin
fi

# Clone or update repository
if [ ! -d "$INSTALL_DIR/.git" ]; then
    log "📥 Cloning Zoe repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
else
    log "📂 Existing installation detected, updating..."
    git -C "$INSTALL_DIR" pull
fi

cd "$INSTALL_DIR"

# Ensure config/.env
if [ ! -f config/.env ]; then
    if [ -f .env.example ]; then
        log "Creating config/.env from example..."
        mkdir -p config
        cp .env.example config/.env
    else
        warn "No .env.example found; skipping environment setup"
    fi
fi

if [ -f config/.env ]; then
    # shellcheck disable=SC1091
    source config/.env
fi

MODEL="${OLLAMA_MODEL:-mistral:7b}"

log "🔄 Pulling latest containers..."
$DOCKER compose pull

log "🚀 Building and starting services..."
$DOCKER compose up -d --build

log "⏳ Waiting for Ollama..."
sleep 30

log "🧠 Downloading model: $MODEL"
$DOCKER exec zoe-ollama ollama pull "$MODEL" || warn "Model pull failed"

IP=$(hostname -I | awk '{print $1}')

log "✅ Installation/upgrade complete!"
echo "----------------------------------------"
echo "🌐 Web UI:  http://$IP:8080"
echo "🔌 API:     http://$IP:8000"
echo "----------------------------------------"
