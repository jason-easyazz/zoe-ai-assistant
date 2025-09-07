#!/bin/bash
echo "🔍 Verifying True Intelligence..."

# Check if core exists
if docker exec zoe-core test -f /app/true_intelligence_core.py; then
    echo "✅ Core module present"
else
    echo "❌ Core module missing!"
fi

# Test real data
TEST=$(curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "show CPU usage"}' | jq -r '.response')

if [[ "$TEST" == *"%"* ]]; then
    echo "✅ Real data working"
else
    echo "❌ Not using real data!"
fi
