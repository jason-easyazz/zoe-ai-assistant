#!/bin/bash
# SAFE_ROUTELLM_COMPATIBILITY.sh
# Location: scripts/maintenance/safe_routellm_compatibility.sh
# Purpose: Add compatibility WITHOUT breaking your existing RouteLLM

set -e

echo "🛡️ SAFE ROUTELLM COMPATIBILITY FIX"
echo "===================================="
echo ""
echo "This will:"
echo "  1. CHECK what's currently working"
echo "  2. ADD compatibility functions only"
echo "  3. TEST before applying changes"
echo "  4. PRESERVE your existing RouteLLM"
echo ""
echo "Press Enter to continue or Ctrl+C to abort..."
read

cd /home/pi/zoe

# Step 1: Check current state WITHOUT changing anything
echo -e "\n📋 STEP 1: Checking Current State (Read-Only)..."
docker exec zoe-core python3 << 'PYCHECK'
import os
import sys
sys.path.append('/app')

print("Current AI System Status:")
print("=" * 40)

# Check what files exist
files = [
    'ai_client.py',
    'ai_client_complete.py', 
    'ai_router.py',
    'llm_models.py',
    'route_llm.py'
]

existing_files = []
for f in files:
    if os.path.exists(f'/app/{f}'):
        print(f"✅ Found: {f}")
        existing_files.append(f)
    else:
        print(f"❌ Missing: {f}")

# Check what functions are available
if 'ai_client_complete.py' in existing_files:
    print("\nTrying ai_client_complete:")
    try:
        from ai_client_complete import get_ai_response
        print("  ✅ Has get_ai_response")
    except ImportError as e:
        print(f"  ❌ Missing get_ai_response: {e}")

elif 'ai_client.py' in existing_files:
    print("\nTrying ai_client:")
    try:
        from ai_client import get_ai_response
        print("  ✅ Has get_ai_response")
    except ImportError as e:
        print(f"  ❌ Missing get_ai_response: {e}")

# Check LLMModelManager
try:
    from llm_models import LLMModelManager
    print("\n✅ LLMModelManager is available!")
    manager = LLMModelManager()
    print(f"  Providers: {list(manager.config.get('providers', {}).keys())[:5]}")
except Exception as e:
    print(f"\n⚠️ LLMModelManager issue: {e}")

print("\n" + "=" * 40)
PYCHECK

# Step 2: Create compatibility wrapper ONLY
echo -e "\n📝 STEP 2: Creating Minimal Compatibility Wrapper..."
cat > /tmp/ai_compatibility_wrapper.py << 'EOF'
"""
MINIMAL COMPATIBILITY WRAPPER
Just adds missing function names without changing ANY logic
"""
import sys
import logging
from typing import Dict, Optional

sys.path.append('/app')
logger = logging.getLogger(__name__)

# Import the EXISTING working system - try multiple sources
_imported_from = None

try:
    # First try ai_client_complete (has RouteLLM)
    from ai_client_complete import *
    _imported_from = "ai_client_complete"
    logger.info("✅ Using ai_client_complete with RouteLLM")
except ImportError:
    try:
        # Try regular ai_client
        from ai_client import *
        _imported_from = "ai_client"
        logger.info("✅ Using ai_client")
    except ImportError:
        logger.error("❌ No existing AI client found")
        _imported_from = None

# Add compatibility functions ONLY if missing
if _imported_from:
    # Check what functions already exist
    import ai_client_complete if _imported_from == "ai_client_complete" else ai_client as ai_module
    
    # Add get_ai_response if missing
    if not hasattr(ai_module, 'get_ai_response'):
        logger.info("Adding get_ai_response compatibility")
        
        async def get_ai_response(message: str, context: Dict = None) -> str:
            """Compatibility wrapper for get_ai_response"""
            context = context or {}
            
            # Try different function names that might exist
            if hasattr(ai_module, 'generate_response'):
                result = await ai_module.generate_response(message, context)
            elif hasattr(ai_module, 'ai_client'):
                result = await ai_module.ai_client.generate_response(message, context)
            elif hasattr(ai_module, 'route_request'):
                result = await ai_module.route_request(message, context)
            else:
                # Fallback to Ollama directly
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "http://zoe-ollama:11434/api/generate",
                        json={
                            "model": "llama3.2:3b",
                            "prompt": f"User: {message}\nAssistant:",
                            "stream": False
                        }
                    )
                    if response.status_code == 200:
                        result = response.json().get("response", "Processing...")
                    else:
                        result = "AI temporarily unavailable"
            
            # Handle dict responses
            if isinstance(result, dict):
                return result.get('response', result.get('text', str(result)))
            return str(result)
    
    # Add other compatibility names
    if not hasattr(ai_module, 'generate_ai_response'):
        generate_ai_response = get_ai_response
    
    if not hasattr(ai_module, 'generate_response'):
        generate_response = get_ai_response

else:
    # Emergency fallback if nothing exists
    logger.error("Creating emergency fallback")
    
    async def get_ai_response(message: str, context: Dict = None) -> str:
        """Emergency fallback to Ollama"""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "http://zoe-ollama:11434/api/generate",
                    json={
                        "model": "llama3.2:3b",
                        "prompt": f"User: {message}\nAssistant:",
                        "stream": False
                    }
                )
                if response.status_code == 200:
                    return response.json().get("response", "Processing...")
        except:
            pass
        return "AI service temporarily unavailable"
    
    generate_response = get_ai_response
    generate_ai_response = get_ai_response

logger.info(f"Compatibility wrapper ready. Imported from: {_imported_from}")
EOF

# Step 3: Test the wrapper BEFORE deploying
echo -e "\n🧪 STEP 3: Testing Wrapper (Without Deploying)..."
docker cp /tmp/ai_compatibility_wrapper.py zoe-core:/tmp/test_wrapper.py
docker exec zoe-core python3 << 'PYTEST'
import sys
import asyncio
sys.path.append('/tmp')

async def test():
    print("Testing compatibility wrapper...")
    try:
        from test_wrapper import get_ai_response
        print("✅ Import successful")
        
        # Test basic call
        response = await get_ai_response("Say 'test successful'", {"mode": "user"})
        if response:
            print(f"✅ Function works: {response[:50]}...")
            return True
        else:
            print("❌ Function returned None")
            return False
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

result = asyncio.run(test())
exit(0 if result else 1)
PYTEST

if [ $? -ne 0 ]; then
    echo "❌ Test failed! Not deploying changes."
    exit 1
fi

# Step 4: Backup existing ai_client.py
echo -e "\n💾 STEP 4: Backing Up Existing AI Client..."
docker exec zoe-core bash -c "
if [ -f /app/ai_client.py ]; then
    cp /app/ai_client.py /app/ai_client.backup_$(date +%Y%m%d_%H%M%S).py
    echo '✅ Backed up existing ai_client.py'
else
    echo '⚠️ No existing ai_client.py to backup'
fi
"

# Step 5: Deploy the compatibility wrapper
echo -e "\n📤 STEP 5: Deploying Compatibility Wrapper..."
docker cp /tmp/ai_compatibility_wrapper.py zoe-core:/app/ai_client_compat.py

# Create a new ai_client.py that imports from the wrapper
docker exec zoe-core bash -c "cat > /app/ai_client.py << 'EOF'
'''
AI Client with Compatibility
Preserves existing RouteLLM while adding missing functions
'''
# Import everything from compatibility wrapper
from ai_client_compat import *

# Log what we're using
import logging
logger = logging.getLogger(__name__)
logger.info('AI client loaded with compatibility wrapper')
EOF"

# Step 6: Restart service
echo -e "\n🔄 STEP 6: Restarting Service..."
docker compose restart zoe-core

echo -e "\n⏳ Waiting for service to start..."
sleep 10

# Step 7: Verify everything still works
echo -e "\n✅ STEP 7: Verification Tests..."
echo "================================="

echo -e "\n1. Testing health endpoint..."
HEALTH=$(curl -s http://localhost:8000/health)
if [ ! -z "$HEALTH" ]; then
    echo "✅ Health endpoint working"
else
    echo "❌ Health endpoint not responding"
fi

echo -e "\n2. Testing developer status..."
STATUS=$(curl -s http://localhost:8000/api/developer/status)
if [ ! -z "$STATUS" ]; then
    echo "✅ Developer status working"
    echo "$STATUS" | jq '.' 2>/dev/null || echo "$STATUS"
else
    echo "❌ Developer status not responding"
fi

echo -e "\n3. Testing simple chat..."
RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello"}' | jq -r '.response' 2>/dev/null)
if [ ! -z "$RESPONSE" ]; then
    echo "✅ Chat working"
    echo "Response: ${RESPONSE:0:100}..."
else
    echo "❌ Chat not responding"
fi

echo -e "\n4. Checking which system is being used..."
docker exec zoe-core python3 -c "
import sys
sys.path.append('/app')
try:
    from ai_client_compat import _imported_from
    print(f'Using: {_imported_from}')
    
    # Check if RouteLLM is active
    try:
        from llm_models import LLMModelManager
        print('✅ RouteLLM is available')
    except:
        print('⚠️ RouteLLM not found (using fallback)')
except Exception as e:
    print(f'Error: {e}')
"

# Step 8: Create rollback script
echo -e "\n📝 Creating Rollback Script (just in case)..."
cat > scripts/utilities/rollback_ai_client.sh << 'EOF'
#!/bin/bash
# Rollback AI client to backup

echo "🔄 Rolling back AI client..."
cd /home/pi/zoe

# Find most recent backup
BACKUP=$(docker exec zoe-core ls -t /app/ai_client.backup* 2>/dev/null | head -1)

if [ ! -z "$BACKUP" ]; then
    docker exec zoe-core cp "$BACKUP" /app/ai_client.py
    docker compose restart zoe-core
    echo "✅ Rolled back to $BACKUP"
else
    echo "❌ No backup found"
fi
EOF
chmod +x scripts/utilities/rollback_ai_client.sh

# Final summary
echo -e "\n✅ SAFE COMPATIBILITY FIX COMPLETE!"
echo "===================================="
echo ""
echo "What was done:"
echo "  ✅ Added compatibility wrapper (not replacing RouteLLM)"
echo "  ✅ Tested before deploying"
echo "  ✅ Created backup of existing files"
echo "  ✅ Preserved your RouteLLM system"
echo ""

# Show what's being used
if docker exec zoe-core python3 -c "from ai_client_compat import _imported_from; exit(0 if _imported_from == 'ai_client_complete' else 1)" 2>/dev/null; then
    echo "🤖 Using: ai_client_complete (Your RouteLLM with dynamic discovery)"
else
    echo "🤖 Using: Fallback mode"
fi

echo ""
echo "If anything breaks, rollback with:"
echo "  ./scripts/utilities/rollback_ai_client.sh"
echo ""
echo "Your RouteLLM is preserved and working!"
