#!/bin/bash
# CHECK_DEVELOPER_STATE.sh
# Location: scripts/maintenance/check_developer_state.sh
# Purpose: Check current state before making changes

echo "🔍 CHECKING DEVELOPER SYSTEM STATE"
echo "==================================="
echo ""

cd /home/pi/zoe

# 1. Check if containers are running
echo "1️⃣ Docker containers status:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "(NAMES|zoe-)"
echo ""

# 2. Check if developer router already exists
echo "2️⃣ Checking for existing developer router:"
if [ -f services/zoe-core/routers/developer.py ]; then
    echo "✅ Developer router exists"
    echo "First 20 lines:"
    head -20 services/zoe-core/routers/developer.py
else
    echo "❌ No developer router found"
fi
echo ""

# 3. Check main.py for developer router inclusion
echo "3️⃣ Checking main.py for developer router:"
if [ -f services/zoe-core/main.py ]; then
    echo "Searching for developer imports:"
    grep -n "developer" services/zoe-core/main.py || echo "No developer references found"
    echo ""
    echo "Current routers included:"
    grep -n "include_router\|from routers" services/zoe-core/main.py || echo "No routers found"
else
    echo "❌ main.py not found!"
fi
echo ""

# 4. Check what endpoints are currently available
echo "4️⃣ Testing current API endpoints:"
echo "Main health:"
curl -s http://localhost:8000/health | jq '.' 2>/dev/null || echo "❌ Health endpoint not responding"
echo ""

echo "API health:"
curl -s http://localhost:8000/api/health | jq '.' 2>/dev/null || echo "❌ API health not responding"
echo ""

echo "Developer status (if exists):"
curl -s http://localhost:8000/api/developer/status | jq '.' 2>/dev/null || echo "❌ Developer status not found"
echo ""

echo "Available routes:"
curl -s http://localhost:8000/openapi.json | jq '.paths | keys' 2>/dev/null || echo "❌ Cannot get route list"
echo ""

# 5. Check nginx configuration
echo "5️⃣ Checking nginx configuration:"
if [ -f services/zoe-ui/nginx.conf ]; then
    echo "nginx.conf exists. Proxy rules:"
    grep -A 2 "location /api" services/zoe-ui/nginx.conf
else
    echo "Using default nginx config (checking container):"
    docker exec zoe-ui cat /etc/nginx/conf.d/default.conf 2>/dev/null | grep -A 2 "location /api" || echo "❌ Cannot check nginx config"
fi
echo ""

# 6. Check frontend files
echo "6️⃣ Checking frontend developer files:"
echo "Developer directory contents:"
ls -la services/zoe-ui/dist/developer/ 2>/dev/null | head -10 || echo "❌ Developer directory not found"
echo ""

echo "Checking API_BASE in index.html:"
grep -n "API_BASE\|fetch.*api" services/zoe-ui/dist/developer/index.html 2>/dev/null | head -5 || echo "No API calls found"
echo ""

# 7. Check for any error logs
echo "7️⃣ Recent error logs from zoe-core:"
docker logs zoe-core --tail 20 2>&1 | grep -E "(ERROR|error|Error)" || echo "No recent errors"
echo ""

# 8. Test if developer chat endpoint responds
echo "8️⃣ Testing developer chat endpoint directly:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "test"}' | jq '.' 2>/dev/null || echo "❌ Developer chat endpoint not responding"
echo ""

# 9. Check if there's an ai_client.py or similar
echo "9️⃣ Checking for AI client implementation:"
if [ -f services/zoe-core/ai_client.py ]; then
    echo "✅ ai_client.py exists"
    grep -n "def\|class" services/zoe-core/ai_client.py | head -10
else
    echo "❌ No ai_client.py found"
fi
echo ""

# 10. Summary
echo "📊 SUMMARY:"
echo "=========="
echo "Based on the checks above, we need to determine:"
echo "1. Is the developer router missing or just not working?"
echo "2. Is main.py properly configured?"
echo "3. Are the endpoints registered but returning 404?"
echo "4. Is nginx proxying correctly?"
echo "5. Is the frontend pointing to the right endpoints?"
echo ""
echo "Run the fix script ONLY if developer endpoints are missing."
echo "If they exist but aren't working, we need a different approach."
