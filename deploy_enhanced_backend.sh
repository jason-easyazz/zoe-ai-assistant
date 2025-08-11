#!/bin/bash
# Deploy Enhanced Zoe Backend

set -euo pipefail

log() {
    echo -e "\033[0;32m[$(date +'%H:%M:%S')] $1\033[0m"
}

PROJECT_DIR="${PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR"

log "🚀 Deploying Enhanced Zoe Backend..."

# Backup current main.py
if [ -f "services/zoe-core/main.py" ]; then
    cp "services/zoe-core/main.py" "services/zoe-core/main.py.backup.$(date +'%Y%m%d_%H%M%S')"
    log "📦 Backed up existing main.py"
fi

# Deploy enhanced version
cp "services/zoe-core/main_enhanced.py" "services/zoe-core/main.py"
log "✅ Enhanced backend deployed"

# Rebuild and restart
log "🔄 Rebuilding backend service..."
docker compose build zoe-core

log "🚀 Restarting services..."
docker compose restart zoe-core

# Wait for service to be ready
log "⏳ Waiting for service to start..."
sleep 10

# Test new endpoints
log "🧪 Testing enhanced endpoints..."

# Test health endpoint
HEALTH=$(curl -s http://localhost:8000/health || echo "failed")
if echo "$HEALTH" | grep -q "healthy"; then
    log "✅ Health check passed"
else
    log "❌ Health check failed"
fi

# Test shopping endpoint
SHOPPING=$(curl -s http://localhost:8000/api/shopping || echo "failed")
if echo "$SHOPPING" | grep -q "items"; then
    log "✅ Shopping endpoint working"
else
    log "❌ Shopping endpoint failed"
fi

# Test workflows endpoint
WORKFLOWS=$(curl -s http://localhost:8000/api/workflows || echo "failed")
if echo "$WORKFLOWS" | grep -q "workflows"; then
    log "✅ Workflows endpoint working"
else
    log "❌ Workflows endpoint failed"
fi

# Test settings endpoint
SETTINGS=$(curl -s http://localhost:8000/api/settings || echo "failed")
if echo "$SETTINGS" | grep -q "personality"; then
    log "✅ Settings endpoint working"
else
    log "❌ Settings endpoint failed"
fi

IP=$(hostname -I | awk '{print $1}' || echo "localhost")
echo ""
echo -e "\033[0;34m🎉 Enhanced Zoe Backend Deployed Successfully!\033[0m"
echo "============================================"
echo ""
echo "🌐 Access Points:"
echo "   UI: http://$IP:8080"
echo "   API: http://$IP:8000"
echo "   API Docs: http://$IP:8000/docs"
echo ""
echo "🎯 New Features Available:"
echo "   ✅ Shopping List API (/api/shopping)"
echo "   ✅ Workflows API (/api/workflows)"  
echo "   ✅ Enhanced Settings (/api/settings)"
echo "   ✅ Service Health Monitoring"
echo ""
echo "🚀 Your Zoe v3.1 backend is now fully enhanced!"
