#!/bin/bash
# FIX_ZACK_COMPLETE.sh
# Location: scripts/maintenance/fix_zack_complete.sh
# Purpose: Complete diagnostic and fix for Zack code generation

set -e

echo "🔧 COMPLETE ZACK FIX - DIAGNOSE AND REPAIR"
echo "=========================================="
echo ""

cd /home/pi/zoe

# Step 1: Diagnostics
echo "📊 STEP 1: RUNNING DIAGNOSTICS"
echo "-------------------------------"

echo -e "\n1. Checking container status..."
docker ps | grep zoe-core || echo "❌ zoe-core not running"

echo -e "\n2. Checking AI modules..."
docker exec zoe-core python3 -c "
import sys
sys.path.append('/app')
import os

print('AI Files present:')
files = [f for f in os.listdir('/app') if 'ai' in f.lower() or 'llm' in f.lower()]
for f in files[:5]:
    print(f'  • {f}')

# Test imports
try:
    from ai_client_complete import get_ai_response
    print('✅ ai_client_complete: WORKING')
except Exception as e:
    print(f'❌ ai_client_complete: {e}')

try:
    from ai_client import ai_client
    print('✅ ai_client: FOUND')
except:
    print('❌ ai_client: MISSING')
"

echo -e "\n3. Testing AI response..."
docker exec zoe-core python3 << 'PYTEST'
import asyncio
import sys
sys.path.append('/app')

async def test():
    try:
        from ai_client_complete import get_ai_response
        response = await get_ai_response('Say "working"', {"mode": "developer"})
        if response:
            print(f"✅ AI responds: {response[:50]}...")
        else:
            print("❌ AI returned None")
    except Exception as e:
        print(f"❌ AI error: {e}")

asyncio.run(test())
PYTEST

echo -e "\n4. Checking current developer.py..."
docker exec zoe-core head -20 /app/routers/developer.py 2>/dev/null || echo "❌ No developer.py found"

# Step 2: Create the WORKING fix
echo -e "\n📝 STEP 2: CREATING FIXED DEVELOPER.PY"
echo "---------------------------------------"

cat > /tmp/developer_fixed.py << 'DEVPY'
"""
Developer Router - FIXED for actual code generation
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import sys
import os
import re
from datetime import datetime
import logging

sys.path.append('/app')

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer", tags=["developer"])

class DeveloperChat(BaseModel):
    message: str

@router.get("/status")
async def get_status():
    """Check developer system status"""
    return {
        "status": "operational",
        "mode": "code-generator",
        "personality": "Zack",
        "timestamp": datetime.now().isoformat()
    }

@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """Generate ACTUAL code using AI"""
    
    try:
        # Import the working AI
        from ai_client_complete import get_ai_response
        
        # Create code-forcing prompt
        if any(word in request.message.lower() for word in ['build', 'create', 'implement', 'fix', 'endpoint', 'api', 'function']):
            # Force code output
            prompt = f"""You are Zack, a developer AI. OUTPUT ONLY CODE.

User wants: {request.message}

Requirements:
1. Start with: # File: /app/routers/[name].py
2. Include ALL imports
3. Create COMPLETE, WORKING code
4. Add error handling
5. NO explanations outside code comments

Example format:
```python
# File: /app/routers/users.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/users")

@router.get("/")
async def get_users():
    return {"users": []}
```

NOW OUTPUT THE COMPLETE CODE:"""
        else:
            # Regular technical response
            prompt = f"You are Zack, a technical AI. {request.message}"
        
        # Get AI response
        response = await get_ai_response(
            prompt,
            {"mode": "developer", "temperature": 0.2}
        )
        
        # Extract code blocks if present
        code_blocks = []
        if response and "```" in response:
            matches = re.findall(r'```(?:python|bash|javascript|yaml)?\n(.*?)```', response, re.DOTALL)
            code_blocks = matches
        
        # Return structured response
        return {
            "response": response if response else "No response from AI",
            "has_code": len(code_blocks) > 0,
            "code_blocks": code_blocks[:3] if code_blocks else None
        }
        
    except ImportError as e:
        logger.error(f"Import error: {e}")
        
        # Try alternate AI
        try:
            from ai_client import ai_client
            response = await ai_client.generate_response(
                request.message,
                {"mode": "developer"}
            )
            return {"response": response, "alternate_ai": True}
        except:
            pass
        
        # Final fallback
        return {
            "response": f"""# File: /app/routers/generated.py
# AI unavailable - Template provided
from fastapi import APIRouter

router = APIRouter(prefix="/api/generated")

@router.get("/")
async def generated_endpoint():
    \"\"\"TODO: Implement {request.message}\"\"\"
    return {"message": "Template for: " + request.message[:50]}
""",
            "error": str(e),
            "fallback": True
        }
        
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {
            "response": f"Error: {e}",
            "error": str(e)
        }

@router.post("/analyze")
async def analyze_request(request: DeveloperChat):
    """Analyze what needs to be built"""
    
    try:
        from ai_client_complete import get_ai_response
        
        analysis_prompt = f"""Analyze this request and list the code files needed: {request.message}
        
Output a structured list of files to create."""
        
        response = await get_ai_response(analysis_prompt, {"mode": "developer"})
        
        return {
            "analysis": response,
            "request": request.message
        }
    except Exception as e:
        return {"error": str(e)}
DEVPY

echo "✅ Created fixed developer.py"

# Step 3: Deploy the fix
echo -e "\n🚀 STEP 3: DEPLOYING FIX"
echo "------------------------"

echo "Copying to container..."
docker cp /tmp/developer_fixed.py zoe-core:/app/routers/developer.py

echo "Restarting service..."
docker restart zoe-core

echo "Waiting for service to start..."
sleep 10

# Step 4: Test the fix
echo -e "\n🧪 STEP 4: TESTING FIX"
echo "----------------------"

echo -e "\n1. Testing status endpoint..."
curl -s http://localhost:8000/api/developer/status | jq '.' || echo "❌ Status endpoint failed"

echo -e "\n2. Testing code generation..."
TEST_RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Build a simple health check endpoint"}' 2>/dev/null)

if [ -n "$TEST_RESPONSE" ]; then
    echo "$TEST_RESPONSE" | jq '.has_code' 2>/dev/null || echo "$TEST_RESPONSE"
    echo -e "\nFirst 500 chars of response:"
    echo "$TEST_RESPONSE" | jq -r '.response' 2>/dev/null | head -c 500
else
    echo "❌ No response received"
fi

echo -e "\n\n✅ COMPLETE! Test Zack with:"
echo ""
echo "curl -X POST http://localhost:8000/api/developer/chat \\"
echo "  -H 'Content-Type: application/json' \\"
echo "  -d '{\"message\": \"Build a user authentication system\"}'"
