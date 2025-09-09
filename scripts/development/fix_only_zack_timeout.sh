#!/bin/bash
# FIX_ONLY_ZACK_TIMEOUT.sh
# Minimal fix - just prevent Zack from timing out

echo "🔧 FIXING ONLY ZACK TIMEOUT"
echo "==========================="
echo ""

cd /home/pi/zoe

# Just patch the timeout and response length in ai_client
docker exec zoe-core bash -c "sed -i 's/timeout=60.0/timeout=20.0/g' /app/ai_client.py"
docker exec zoe-core bash -c "sed -i 's/timeout=30.0/timeout=20.0/g' /app/ai_client.py"

# Add num_predict limit to Ollama calls if not present
docker exec zoe-core python3 << 'PATCH'
with open('/app/ai_client.py', 'r') as f:
    content = f.read()

# Only if num_predict is not already there
if '"num_predict"' not in content:
    content = content.replace(
        '"stream": False',
        '"stream": False,\n                    "options": {"num_predict": 300}'
    )
    
    with open('/app/ai_client.py', 'w') as f:
        f.write(content)
    print("Added response limit")
else:
    print("Response limit already set")
PATCH

# Restart
docker compose restart zoe-core
sleep 10

# Test ONLY Zack
echo "Testing Zack fix:"
curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Write a simple hello world function"}' | jq -r '.response'
