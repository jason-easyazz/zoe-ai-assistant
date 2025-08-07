#!/bin/bash
echo "🧪 Testing Zoe v3.1 Backend"
echo "=========================="

# Get your Pi's IP
IP=$(hostname -I | awk '{print $1}')

# Test health endpoint
echo "1. Testing health check..."
curl -s http://localhost:8000/health | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(f'✅ Health: {data[\"status\"]} - Version: {data[\"version\"]}')
except Exception as e:
    print(f'❌ Health check failed: {e}')
"

# Test settings
echo "2. Testing settings..."
curl -s http://localhost:8000/api/settings | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(f'✅ Settings: Fun level {data[\"personality_fun\"]}')
except Exception as e:
    print(f'❌ Settings failed: {e}')
"

# Test chat
echo "3. Testing chat..."
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello Zoe!"}' | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(f'✅ Chat working: {data[\"response\"][:50]}...')
except Exception as e:
    print(f'❌ Chat failed: {e}')
"

echo ""
echo "🌐 Access Points:"
echo "   Health: http://$IP:8000/health"
echo "   API Docs: http://$IP:8000/docs"
echo "   Chat Test: http://$IP:8000/api/chat"

echo ""
echo "📊 Container Status:"
docker compose ps
