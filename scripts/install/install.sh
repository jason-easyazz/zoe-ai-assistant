#!/bin/bash
set -euo pipefail

log() {
    echo "[$(date +'%H:%M:%S')] $1"
}

cd "$(dirname "$0")/../.."

log "🤖 Starting Zoe v3.1 Installation"
echo "=================================="

if [[ -f config/.env ]]; then
    source config/.env
    log "✅ Environment loaded"
else
    echo "❌ Environment file not found!"
    exit 1
fi

log "🏗️ Building and starting services..."
cd config
docker compose up -d

log "⏳ Waiting for Ollama..."
sleep 30

log "🧠 Downloading AI model..."
docker exec zoe-ollama ollama pull mistral:7b

IP=$(hostname -I | awk '{print $1}')
echo ""
log "🎉 Zoe v3.1 Foundation Ready!"
echo "=================================="
echo ""
echo "🌐 Access Points:"
echo "   Web UI: http://$IP:8080"
echo "   API: http://$IP:8000"
echo ""
echo "📚 Note: Basic stubs running - full features come in Chat 2-4!"
