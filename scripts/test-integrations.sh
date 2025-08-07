#!/bin/bash
# Zoe v3.1 Integration Testing Suite
set -euo pipefail

readonly GREEN='\033[0;32m'
readonly RED='\033[0;31m'
readonly YELLOW='\033[1;33m'
readonly NC='\033[0m'

success() { echo -e "${GREEN}✅ $1${NC}"; }
warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
error() { echo -e "${RED}❌ $1${NC}"; }

echo "🧪 Zoe v3.1 Integration Testing Suite"
echo "====================================="
echo ""

# Test Core API
echo "Testing Core API..."
if curl -s http://localhost:8000/health | jq -e '.status == "healthy"' > /dev/null 2>&1; then
    success "Core API responding"
    
    # Test enhanced chat
    CHAT_RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat \
        -H "Content-Type: application/json" \
        -d '{"message": "Hello Zoe! Test message."}')
    
    if echo "$CHAT_RESPONSE" | jq -e '.response' > /dev/null 2>&1; then
        success "Enhanced chat working"
    else
        error "Enhanced chat failed"
    fi
else
    error "Core API not responding"
fi

# Test Voice Services
echo ""
echo "Testing Voice Services..."

# Test Whisper STT
if curl -s http://localhost:9001/health | jq -e '.status' > /dev/null 2>&1; then
    success "Whisper STT service responding"
else
    warning "Whisper STT service not ready (may still be loading models)"
fi

# Test Coqui TTS
if curl -s http://localhost:9002/health | jq -e '.status' > /dev/null 2>&1; then
    success "Coqui TTS service responding"
else
    warning "Coqui TTS service not ready (may still be loading models)"
fi

# Test n8n
echo ""
echo "Testing n8n Automation..."
if curl -s http://localhost:5678/healthz > /dev/null 2>&1; then
    success "n8n automation service healthy"
else
    error "n8n automation service not responding"
fi

# Test Home Assistant
echo ""
echo "Testing Home Assistant..."
if curl -s http://localhost:8123 > /dev/null 2>&1; then
    success "Home Assistant responding"
else
    warning "Home Assistant not ready (may still be starting up)"
fi

# Test Matrix Service
echo ""
echo "Testing Matrix Service..."
if curl -s http://localhost:9003/health | jq -e '.status' > /dev/null 2>&1; then
    success "Matrix service responding"
else
    warning "Matrix service not ready"
fi

# Test Frontend Integration
echo ""
echo "Testing Frontend Integration..."
if curl -s http://localhost:8080 | grep -q "Zoe AI Assistant"; then
    success "Frontend UI loading"
else
    error "Frontend UI not responding"
fi

# Test Database
echo ""
echo "Testing Database Integration..."
DB_TEST=$(curl -s http://localhost:8000/api/dashboard)
if echo "$DB_TEST" | jq -e '.integrations' > /dev/null 2>&1; then
    success "Database integration working"
else
    error "Database integration failed"
fi

# Service Status Summary
echo ""
echo "📊 Service Status Summary:"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "🔗 Integration Health:"
curl -s http://localhost:8000/health | jq '.integrations' 2>/dev/null || echo "Could not fetch integration status"

echo ""
echo "💡 Quick Test Commands:"
echo "   Test Chat: curl -X POST http://localhost:8000/api/chat -H 'Content-Type: application/json' -d '{\"message\":\"Hello Zoe!\"}'"
echo "   View Logs: docker compose logs -f [service-name]"
echo "   Restart Service: docker compose restart [service-name]"
echo "   Full Restart: docker compose restart"
echo ""
echo "🎯 Access your complete Zoe v3.1 at: http://$(hostname -I | awk '{print $1}'):8080"
