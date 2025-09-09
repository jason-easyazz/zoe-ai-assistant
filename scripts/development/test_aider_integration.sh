#!/bin/bash
# Test Aider Web Integration

echo "🧪 TESTING AIDER WEB INTEGRATION"
echo "================================"

# Test 1: Create session
echo -e "\n1. Creating Aider session..."
SESSION=$(curl -s -X POST http://localhost:8000/api/aider/session \
    -H "Content-Type: application/json" \
    -d '{"files": ["services/zoe-core/main.py"]}' | jq -r '.session_id')

if [ ! -z "$SESSION" ]; then
    echo "✅ Session created: $SESSION"
else
    echo "❌ Failed to create session"
    exit 1
fi

# Test 2: Send message
echo -e "\n2. Sending test message..."
RESPONSE=$(curl -s -X POST http://localhost:8000/api/aider/chat \
    -H "Content-Type: application/json" \
    -d "{\"session_id\": \"$SESSION\", \"message\": \"Show me the structure of main.py\"}")

if [ ! -z "$RESPONSE" ]; then
    echo "✅ Got response from Aider"
    echo "$RESPONSE" | jq '.content' | head -20
else
    echo "❌ No response from Aider"
fi

# Test 3: Check task integration
echo -e "\n3. Testing task integration..."
curl -s http://localhost:8000/api/tasks | jq '.tasks[0]' || echo "No tasks yet"

echo -e "\n✅ Integration test complete!"
echo "Access Aider at: http://192.168.1.60:8080/developer/aider.html"
