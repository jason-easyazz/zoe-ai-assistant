#!/bin/bash
# TEST_ROUTELLM_AND_ZACK.sh
# Test intelligent routing and Zack's full capabilities

echo "🧪 TESTING ROUTELLM & ZACK'S DEVELOPER POWERS"
echo "=============================================="
echo ""

cd /home/pi/zoe

# Test 1: Check if RouteLLM is loaded
echo "1️⃣ Checking RouteLLM status:"
docker exec zoe-core ls -la | grep route_llm && echo "  ✅ RouteLLM file exists"
docker exec zoe-core python3 -c "import route_llm; print('  ✅ RouteLLM imports')" 2>/dev/null || echo "  ❌ RouteLLM not loaded"

# Test 2: Simple vs Complex Query Routing
echo -e "\n2️⃣ Testing query complexity routing:"
echo "Simple query (should use fast model):"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What time is it?"}' | jq -r '.response' | head -50

echo -e "\nComplex query (should use advanced model):"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Design a complete microservices architecture for a real-time chat system with Redis pub/sub, WebSocket connections, and horizontal scaling"}' | jq -r '.response' | head -200

# Test 3: Zack's Building Capabilities
echo -e "\n3️⃣ Testing Zack's BUILDING power:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Build a new API endpoint for Zoe that tracks user mood over time. Include the complete FastAPI router code, database schema, and frontend JavaScript"}' | jq -r '.response'

# Test 4: Zack's Fixing Capabilities
echo -e "\n4️⃣ Testing Zack's FIXING power:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "The Zoe chat is slow. Diagnose potential causes and provide a script to fix performance issues"}' | jq -r '.response'

# Test 5: Zack's Design Capabilities
echo -e "\n5️⃣ Testing Zack's DESIGN power:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Design a plugin system for Zoe that allows third-party developers to add features. Include architecture diagram in ASCII and implementation plan"}' | jq -r '.response'
