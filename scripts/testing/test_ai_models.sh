#!/bin/bash
# TEST_AI_MODELS.sh
# Location: scripts/testing/test_ai_models.sh
# Purpose: Detailed test of which AI models are actually being used

set -e

echo "🧪 DETAILED AI MODEL TEST"
echo "========================="
echo ""

cd /home/pi/zoe

# First check if keys are loaded in container
echo "1️⃣ Checking if API keys are loaded in container..."
docker exec zoe-core python3 -c "
from api_key_loader import loaded_keys
import os
print('Anthropic key present:', bool(os.getenv('ANTHROPIC_API_KEY')))
print('OpenAI key present:', bool(os.getenv('OPENAI_API_KEY')))
print('Keys loaded:', loaded_keys.keys() if loaded_keys else 'None')
"

echo ""
echo "2️⃣ Testing developer chat with full response..."
RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "What AI model and version are you using right now? Be specific."}')

echo "Full response:"
echo "$RESPONSE" | jq '.'

echo ""
echo "3️⃣ Testing Zoe chat with full response..."
RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Hi Zoe! What AI model powers you?"}')

echo "Full response:"
echo "$RESPONSE" | jq '.'

echo ""
echo "4️⃣ Checking container logs for AI usage..."
docker logs zoe-core --tail 20 | grep -E "Claude|OpenAI|GPT|Anthropic|Using|model" || echo "No model mentions in recent logs"

echo ""
echo "5️⃣ Direct test of AI client..."
docker exec zoe-core python3 -c "
import asyncio
import sys
sys.path.append('/app')

async def test():
    try:
        from ai_client import ai_client
        
        # Test developer mode
        dev_response = await ai_client.generate_response(
            'Test message',
            {'mode': 'developer'}
        )
        print('Developer mode:', dev_response.get('model', 'unknown'))
        
        # Test user mode  
        user_response = await ai_client.generate_response(
            'Test message',
            {'mode': 'user'}
        )
        print('User mode:', user_response.get('model', 'unknown'))
        
    except ImportError:
        print('ai_client not found, checking for ai_router...')
        try:
            from ai_router import AIRouter
            router = AIRouter()
            if hasattr(router, 'claude_client'):
                print('Claude client exists:', router.claude_client is not None)
            else:
                print('No Claude client in router')
        except ImportError:
            print('No AI router found')

asyncio.run(test())
" 2>&1

echo ""
echo "6️⃣ Checking Anthropic library installation..."
docker exec zoe-core python3 -c "
try:
    import anthropic
    print('✅ Anthropic library installed')
    print('Version:', anthropic.__version__)
except ImportError:
    print('❌ Anthropic library NOT installed')
"

echo ""
echo "7️⃣ Quick install check (if needed)..."
if docker exec zoe-core python3 -c "import anthropic" 2>/dev/null; then
    echo "✅ Anthropic already installed"
else
    echo "📦 Installing Anthropic library..."
    docker exec zoe-core pip install anthropic
    docker restart zoe-core
    sleep 10
    echo "✅ Anthropic installed and service restarted"
fi

echo ""
echo "========================="
echo "📊 TEST SUMMARY"
echo "========================="
echo ""
echo "Check the results above to see:"
echo "  • Are API keys actually loaded? ✓"
echo "  • Is Anthropic library installed?"
echo "  • What model is responding?"
echo "  • Any errors in the logs?"
echo ""
echo "If Anthropic is not installed, the script installed it."
echo "Run this test again after installation completes."
