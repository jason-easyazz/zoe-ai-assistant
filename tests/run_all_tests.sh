#!/bin/bash

echo "🧪 Running Zoe Test Suite..."

# Unit tests
echo -e "\n📝 Running unit tests..."
python3 tests/unit/test_memory.py

# Integration tests
echo -e "\n🔗 Running integration tests..."
bash tests/integration/test_voice_integration.sh

# Performance tests
echo -e "\n⚡ Running performance tests..."
python3 tests/performance/test_api_performance.py

# API tests
echo -e "\n🌐 Testing all API endpoints..."
curl -s http://localhost:8000/health | jq '.'
curl -s http://localhost:8000/api/memory/search -X POST \
  -H "Content-Type: application/json" \
  -d '{"query": "test"}' | jq '.'

echo -e "\n✅ All tests completed!"
