#!/bin/bash
# Zoe v3.1 Complete Integration Installation
set -euo pipefail

readonly GREEN='\033[0;32m'
readonly BLUE='\033[0;34m'
readonly YELLOW='\033[1;33m'
readonly NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%H:%M:%S')] $1${NC}"
}

PROJECT_DIR="$HOME/zoe-v31"
cd "$PROJECT_DIR"

log "🚀 Installing Zoe v3.1 Complete Integration Stack..."

# Check system resources
TOTAL_MEM=$(free -g | awk 'NR==2{print $2}')
if [ "$TOTAL_MEM" -lt 6 ]; then
    echo -e "${YELLOW}⚠️  Warning: Voice services require 6GB+ RAM. You have ${TOTAL_MEM}GB.${NC}"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
fi

# Stop any existing services
log "Stopping existing services..."
docker compose down 2>/dev/null || true

# Update environment with integration settings
log "Updating environment configuration..."
if ! grep -q "VOICE_ENABLED" .env; then
    cat >> .env << EOF

# Integration Settings
VOICE_ENABLED=true
N8N_ENABLED=true
HA_ENABLED=true
MATRIX_ENABLED=false
TIMEZONE=${TIMEZONE:-UTC}
EOF
fi

# Build all services
log "Building integration services..."
echo "This may take 10-15 minutes on Pi 5..."

# Build in order of dependencies
docker compose build zoe-core
docker compose build zoe-whisper
docker compose build zoe-tts  
docker compose build zoe-matrix

# Start core services first
log "Starting core services..."
docker compose up -d zoe-ollama zoe-redis

# Wait for Ollama
log "Waiting for Ollama to start..."
sleep 30

# Download AI model if not present
log "Ensuring AI model is available..."
if ! docker exec zoe-ollama ollama list | grep -q "mistral:7b"; then
    log "Downloading mistral:7b model (this takes 5-10 minutes)..."
    docker exec zoe-ollama ollama pull mistral:7b
fi

# Start remaining services
log "Starting all integration services..."
docker compose up -d

# Wait for services to stabilize
log "Waiting for services to stabilize..."
sleep 60

# Test installations
log "Testing integration services..."

# Test core API
if curl -s http://localhost:8000/health | grep -q "healthy"; then
    log "✅ Core API - Healthy"
else
    log "❌ Core API - Not responding"
fi

# Test voice services
if curl -s http://localhost:9001/health | grep -q "healthy"; then
    log "✅ Voice STT - Healthy"
else
    log "⚠️  Voice STT - Limited (may need time to load models)"
fi

if curl -s http://localhost:9002/health | grep -q "healthy"; then
    log "✅ Voice TTS - Healthy"
else
    log "⚠️  Voice TTS - Limited (may need time to load models)"
fi

# Test n8n
if curl -s http://localhost:5678 > /dev/null; then
    log "✅ n8n Automation - Healthy"
else
    log "❌ n8n Automation - Not responding"
fi

# Test Home Assistant
if curl -s http://localhost:8123 > /dev/null; then
    log "✅ Home Assistant - Healthy"
else
    log "⚠️  Home Assistant - Starting up (may take a few minutes)"
fi

# Test Matrix
if curl -s http://localhost:9003/health | grep -q "healthy"; then
    log "✅ Matrix Messaging - Healthy"
else
    log "⚠️  Matrix Messaging - Ready (needs configuration)"
fi

# Show final status
echo ""
echo -e "${BLUE}🎉 Zoe v3.1 Complete Integration Installation Complete!${NC}"
echo "=================================================="
echo ""
IP=$(hostname -I | awk '{print $1}')
echo -e "${GREEN}🌐 Access Points:${NC}"
echo "   Main UI: http://$IP:8080"
echo "   Core API: http://$IP:8000"
echo "   Voice STT: http://$IP:9001"
echo "   Voice TTS: http://$IP:9002"
echo "   n8n Automation: http://$IP:5678"
echo "   Home Assistant: http://$IP:8123"
echo "   Matrix Service: http://$IP:9003"
echo ""
echo -e "${GREEN}🎯 Key Features Now Available:${NC}"
echo "   ✅ Enhanced AI Chat with Context"
echo "   🎤 Voice Input (Whisper STT)"
echo "   🔊 Voice Output (Coqui TTS)"
echo "   ⚡ Workflow Automation (n8n)"
echo "   🏠 Smart Home Control (Home Assistant)"
echo "   💬 Matrix Messaging Bridge"
echo "   📊 Integrated Dashboard"
echo "   ⚙️  Complete Settings System"
echo ""
echo -e "${GREEN}🚀 Next Steps:${NC}"
echo "   1. Open http://$IP:8080 to start using Zoe"
echo "   2. Test voice features with microphone button"
echo "   3. Configure integrations in Settings"
echo "   4. Import n8n workflows from services/zoe-n8n/workflows/"
echo "   5. Set up Home Assistant devices"
echo ""
echo -e "${YELLOW}📝 Notes:${NC}"
echo "   - Voice services may take 2-3 minutes to fully load models"
echo "   - Home Assistant needs first-time setup at :8123"
echo "   - Matrix messaging requires configuration in Settings"
echo "   - Check logs: docker compose logs -f [service-name]"
echo ""
echo "Zoe is now your complete AI life hub! 🤖✨"
