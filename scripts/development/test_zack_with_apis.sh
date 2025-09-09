#!/bin/bash
# TEST_ZACK_WITH_APIS.sh
# Test Zack with API models for complex tasks

echo "🧪 TESTING ZACK WITH API MODELS"
echo "================================"
echo ""

# Test 1: Complex development task
echo "1. Complex Build Request (should use GPT-4 or Claude):"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Build a complete user authentication system with JWT tokens, password hashing, and rate limiting. Include FastAPI router code, database schema, and middleware."}' | \
  jq -r '.response'

echo -e "\n2. Architecture Design (should use advanced model):"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Design a scalable event-driven architecture for Zoe using Redis pub/sub and WebSockets"}' | \
  jq -r '.response' | head -300

# Check logs to see which model was used
echo -e "\n3. Checking which models were used:"
docker logs zoe-core --tail 20 | grep -i "routing to\|model\|gpt\|claude" || echo "No routing logs"

# Test the RouteLLM status in UI
echo -e "\n4. RouteLLM Status:"
curl -s http://localhost:8000/api/settings-ui/routellm/status | jq '.'
