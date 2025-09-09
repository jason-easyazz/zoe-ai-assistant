#!/bin/bash
# CHECK_AND_PREPARE.sh
# Run this FIRST to check your current state

echo "🔍 Checking Current Zoe State for AI Integration"
echo "================================================"

cd /home/pi/zoe

echo -e "\n📁 Current location:"
pwd

echo -e "\n🐳 Docker containers:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

echo -e "\n🌐 API Health:"
curl -s http://localhost:8000/health | jq '.' || echo "API not responding"

echo -e "\n📊 Existing tasks:"
curl -s http://localhost:8000/api/developer/tasks/list 2>/dev/null | jq '.tasks[:3]' || echo "No tasks endpoint yet"

echo -e "\n🔑 API Keys configured:"
if [ -f .env ]; then
    echo "✓ .env file exists"
    grep -q "OPENAI_API_KEY" .env && echo "✓ OpenAI key present" || echo "⚠️ No OpenAI key"
    grep -q "ANTHROPIC_API_KEY" .env && echo "✓ Anthropic key present" || echo "⚠️ No Anthropic key"
else
    echo "⚠️ No .env file - will use local Ollama only"
fi

echo -e "\n🦙 Ollama status:"
curl -s http://localhost:11434/api/tags | jq '.models[].name' 2>/dev/null || echo "Ollama not accessible"

echo -e "\n✅ Pre-check complete!"
