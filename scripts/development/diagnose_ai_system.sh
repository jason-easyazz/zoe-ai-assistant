#!/bin/bash
# DIAGNOSE_AI_SYSTEM.sh
# Find what's broken in the AI system

echo "🔍 DIAGNOSING AI SYSTEM"
echo "======================="
echo ""

cd /home/pi/zoe

# Check 1: Ollama connectivity
echo "1️⃣ Testing Ollama connection:"
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "  ✅ Ollama is reachable"
    echo "  Available models:"
    curl -s http://localhost:11434/api/tags | jq -r '.models[].name' | sed 's/^/    - /'
else
    echo "  ❌ Ollama not reachable"
fi

# Check 2: Test direct Ollama generation
echo -e "\n2️⃣ Testing Ollama generation:"
response=$(curl -s -X POST http://localhost:11434/api/generate \
  -d '{
    "model": "llama3.2:3b",
    "prompt": "Hello",
    "stream": false
  }' | jq -r '.response' 2>/dev/null)

if [ -n "$response" ]; then
    echo "  ✅ Ollama generates responses"
    echo "  Sample: ${response:0:50}..."
else
    echo "  ❌ Ollama generation failed"
fi

# Check 3: API keys in environment
echo -e "\n3️⃣ Checking API keys:"
docker exec zoe-core printenv | grep -E "API_KEY|ANTHROPIC|OPENAI" | sed 's/=.*/=***/' | sed 's/^/    /'

# Check 4: Check ai_client.py
echo -e "\n4️⃣ Checking ai_client.py implementation:"
docker exec zoe-core python3 -c "
import sys
sys.path.append('/app')
try:
    from ai_client import get_ai_response
    print('  ✅ ai_client imports successfully')
except Exception as e:
    print(f'  ❌ Import error: {e}')
"

# Check 5: Test AI generation directly
echo -e "\n5️⃣ Testing AI generation directly:"
docker exec zoe-core python3 << 'TEST_AI'
import asyncio
import sys
sys.path.append('/app')

async def test():
    try:
        from ai_client import get_ai_response
        response = await get_ai_response("Test message", context={"mode": "developer"})
        print(f"  ✅ AI responds: {response[:100]}...")
        return True
    except Exception as e:
        print(f"  ❌ AI error: {str(e)}")
        return False

asyncio.run(test())
TEST_AI

# Check 6: RouteLLM status
echo -e "\n6️⃣ Checking RouteLLM configuration:"
if [ -f "data/llm_models.json" ]; then
    echo "  Found llm_models.json:"
    cat data/llm_models.json | jq '.providers | to_entries[] | select(.value.enabled==true) | .key' 2>/dev/null | sed 's/^/    - /'
else
    echo "  ❌ No llm_models.json found"
fi

# Check 7: Check if RouteLLM router exists
echo -e "\n7️⃣ Checking RouteLLM implementation:"
docker exec zoe-core ls -la | grep -E "route_llm|routellm" || echo "  ❌ No RouteLLM files found"

echo -e "\n📊 DIAGNOSIS SUMMARY"
echo "===================="
