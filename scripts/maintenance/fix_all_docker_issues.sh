#!/bin/bash
# FIX ALL DOCKER ISSUES
set -e

echo "🔧 FIXING ALL DOCKER ACCESS ISSUES"
echo "=================================="

cd /home/pi/zoe

# Fix 1: Restart container
echo "🔄 Restarting zoe-core..."
docker compose restart zoe-core
sleep 10

# Fix 2: Test API
echo "📊 Testing API..."
if curl -s http://localhost:8000/health | grep -q healthy; then
    echo "✅ API is healthy"
else
    echo "❌ API not responding - rebuilding..."
    docker compose up -d --build zoe-core
    sleep 15
fi

# Fix 3: Test Docker with correct path
echo "🐳 Testing Docker commands..."
RESULT=$(curl -s -X POST http://localhost:8000/api/developer/execute \
  -H "Content-Type: application/json" \
  -d '{"command": "docker ps --format \"{{.Names}}\"", "working_dir": "/app"}')

if echo "$RESULT" | grep -q "zoe-"; then
    echo "✅ Docker commands WORKING!"
    echo "Containers found:"
    echo "$RESULT" | jq -r '.stdout'
else
    echo "❌ Still not working. Output:"
    echo "$RESULT"
fi

# Fix 4: Test developer status
echo ""
echo "📊 FINAL STATUS:"
curl -s http://localhost:8000/api/developer/status | jq '.'

echo ""
echo "✅ DONE! Test at: http://192.168.1.60:8080/developer/"
