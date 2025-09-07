#!/bin/bash
# Protect True Intelligence from degradation

echo "🔒 Protecting True Intelligence..."

# Create protected backup
docker exec zoe-core cp /app/true_intelligence_core.py /app/true_intelligence_core.PROTECTED.py
docker exec zoe-core cp /app/routers/developer.py /app/routers/developer.PROTECTED.py

# Create local backup
docker cp zoe-core:/app/true_intelligence_core.py scripts/permanent/true_intelligence_core.py
docker cp zoe-core:/app/routers/developer.py scripts/permanent/developer_with_intelligence.py

# Create verification script
cat > scripts/utilities/verify_intelligence.sh << 'VERIFY'
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
VERIFY

chmod +x scripts/utilities/verify_intelligence.sh
echo "✅ Protection complete"
