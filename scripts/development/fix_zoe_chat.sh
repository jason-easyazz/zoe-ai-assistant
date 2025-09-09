#!/bin/bash
# FIX_ZOE_CHAT.sh
# Diagnose and fix Zoe chat endpoint

echo "🔧 FIXING ZOE CHAT ENDPOINT"
echo "==========================="
echo ""

cd /home/pi/zoe

# Check the error
echo "📋 Checking Zoe endpoint error..."
echo "Testing /api/chat endpoint:"
response=$(curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "test"}' -w "\nHTTP_STATUS:%{http_code}" 2>/dev/null)

http_status=$(echo "$response" | grep "HTTP_STATUS" | cut -d: -f2)
echo "  HTTP Status: $http_status"

if [ "$http_status" = "000" ]; then
    echo "  ❌ Connection failed"
elif [ "$http_status" = "404" ]; then
    echo "  ❌ Endpoint not found"
elif [ "$http_status" = "500" ]; then
    echo "  ❌ Internal server error"
    echo "  Checking logs..."
    docker logs zoe-core --tail 20 | grep -i error || true
elif [ "$http_status" = "422" ]; then
    echo "  ❌ Request format issue"
fi

# Check if chat router exists
echo -e "\n📁 Checking chat router..."
docker exec zoe-core ls -la routers/ | grep chat || echo "  ❌ No chat router found"

# Check main.py includes
echo -e "\n📝 Checking router registration..."
docker exec zoe-core grep -n "chat" main.py || echo "  ❌ Chat not in main.py"

# Quick fix - ensure chat router is registered
echo -e "\n🔧 Attempting quick fix..."
docker exec zoe-core python3 << 'FIX'
import os

# Check and fix main.py
main_file = "/app/main.py"
with open(main_file, 'r') as f:
    content = f.read()

# Check if chat router is imported
if "from routers import" in content and "chat" not in content:
    print("Adding chat router import...")
    content = content.replace(
        "from routers import",
        "from routers import chat,"
    )
    
    # Add include if missing
    if "chat.router" not in content:
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'app.include_router' in line:
                lines.insert(i+1, 'app.include_router(chat.router)')
                break
        content = '\n'.join(lines)
    
    with open(main_file, 'w') as f:
        f.write(content)
    print("✅ Fixed main.py")
else:
    print("✅ Chat router already registered")
FIX

# Restart to apply fix
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core
sleep 10

# Test again
echo -e "\n🧪 Testing Zoe chat again..."
response=$(curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Hello"}' 2>/dev/null)

if echo "$response" | grep -q "response"; then
    echo "✅ Zoe is now working!"
else
    echo "❌ Still not working. Manual fix needed."
    echo ""
    echo "Try checking:"
    echo "  docker logs zoe-core --tail 50"
fi
