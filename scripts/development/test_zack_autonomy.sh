#!/bin/bash
# TEST_ZACK_AUTONOMY.sh
# Test Zack's full system awareness and control

echo "🔍 TESTING ZACK'S AUTONOMOUS CAPABILITIES"
echo "=========================================="
echo ""

cd /home/pi/zoe

# Test 1: File System Awareness
echo "TEST 1: Can Zack see system files?"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "List all Python files in /app/routers/ directory"}' | jq -r '.response' | head -200

# Test 2: System Analysis
echo -e "\nTEST 2: Can Zack analyze the system?"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Analyze the current Zoe architecture and identify potential improvements"}' | jq -r '.response' | head -200

# Test 3: Problem Detection
echo -e "\nTEST 3: Can Zack detect problems?"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Check for any errors in the last 100 lines of logs and suggest fixes"}' | jq -r '.response' | head -200

# Test 4: Feature Development
echo -e "\nTEST 4: Can Zack create new features?"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Create a new API endpoint for tracking user interactions. Provide complete code."}' | jq -r '.response'
