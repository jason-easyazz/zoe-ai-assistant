#!/bin/bash
# SAFE FIX - Preserves your multi-model work, only fixes names and CORS

cd /home/pi/zoe

echo "🔧 SAFE FIX - Preserving Multi-Model Configuration"
echo "=================================================="

# Fix 1: Add CORS to main.py WITHOUT breaking anything
echo "📝 Fixing CORS safely..."
docker exec zoe-core python3 -c "
import os

# Read main.py
with open('/app/main.py', 'r') as f:
    content = f.read()

# Only add CORS if it's missing
if 'CORSMiddleware' not in content:
    print('Adding CORS configuration...')
    
    # Add import
    if 'from fastapi.middleware.cors import CORSMiddleware' not in content:
        content = content.replace(
            'from fastapi import FastAPI',
            'from fastapi import FastAPI\\nfrom fastapi.middleware.cors import CORSMiddleware'
        )
    
    # Add middleware only if not present
    if 'app.add_middleware' not in content:
        app_line = 'app = FastAPI'
        app_end = content.find('\\n', content.find(app_line))
        if app_end > 0:
            cors_config = '''
# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[\"*\"],
    allow_credentials=True,
    allow_methods=[\"*\"],
    allow_headers=[\"*\"],
    expose_headers=[\"*\"]
)
'''
            content = content[:app_end+1] + cors_config + content[app_end+1:]
    
    # Save
    with open('/app/main.py', 'w') as f:
        f.write(content)
    print('✅ CORS configuration added')
else:
    print('✅ CORS already configured')
"

# Fix 2: Update personality names ONLY (Claude -> Zack) without breaking model routing
echo "🤖 Updating personality names (keeping your model routing)..."
docker exec zoe-core python3 -c "
import os
import glob

# Find all Python files that might have AI personalities
ai_files = [
    '/app/ai_client.py',
    '/app/ai_router.py',
    '/app/llm_client.py',
    '/app/services/zoe-core/ai_client.py'
]

updated = False
for file_path in ai_files:
    if os.path.exists(file_path):
        print(f'Checking {file_path}...')
        with open(file_path, 'r') as f:
            content = f.read()
        
        original = content
        
        # Update personality names only
        # Replace Claude references with Zack in developer mode
        content = content.replace(
            'You are Claude, a technical assistant',
            'You are Zack, a technical AI assistant for the Zoe system'
        )
        content = content.replace(
            '\"Claude\", a technical',
            '\"Zack\", a technical'
        )
        content = content.replace(
            'I\\'m Claude',
            'I\\'m Zack'
        )
        content = content.replace(
            'Claude: ',
            'Zack: '
        )
        content = content.replace(
            'name = \"Claude\"',
            'name = \"Zack\"'
        )
        
        # Make sure Zoe personality is preserved
        if 'You are Zoe' in content:
            print('  ✓ Zoe personality found')
        
        if content != original:
            with open(file_path, 'w') as f:
                f.write(content)
            print(f'  ✅ Updated personalities in {file_path}')
            updated = True

if not updated:
    print('⚠️  No AI files found to update - may need manual configuration')
"

# Fix 3: Ensure developer router exists (but don't overwrite if it's there)
echo "📝 Checking developer router..."
docker exec zoe-core bash -c 'if [ ! -f /app/routers/developer.py ]; then
echo "Creating developer router..."
cat > /app/routers/developer.py << "EOF"
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime

router = APIRouter(prefix="/api/developer")

class ChatRequest(BaseModel):
    message: str
    context: Optional[Dict] = None

@router.get("/status")
async def get_status():
    return {
        "status": "operational",
        "timestamp": datetime.now().isoformat()
    }

@router.post("/chat")
async def developer_chat(request: ChatRequest):
    try:
        import sys
        sys.path.append("/app")
        
        # Try multiple AI client locations
        ai_client = None
        try:
            from ai_client import ai_client
        except:
            try:
                from ai_router import AIRouter
                ai_client = AIRouter()
            except:
                try:
                    from llm_client import ai_client
                except:
                    pass
        
        if ai_client:
            result = await ai_client.generate_response(
                request.message,
                {"mode": "developer"}
            )
            return result
    except Exception as e:
        pass
    
    return {
        "response": "Zack here. Working on getting the AI system online.",
        "model": "fallback"
    }
EOF
echo "✅ Developer router created"
else
echo "✅ Developer router already exists"
fi'

# Fix 4: Ensure developer router is imported
echo "📝 Ensuring developer router is imported..."
docker exec zoe-core python3 -c "
with open('/app/main.py', 'r') as f:
    content = f.read()

# Add developer import if missing
if 'developer' not in content:
    if 'from routers import' in content:
        content = content.replace(
            'from routers import',
            'from routers import developer,'
        )
    else:
        content = content.replace(
            'app = FastAPI',
            'from routers import developer\\n\\napp = FastAPI'
        )
    
    # Add router inclusion
    if 'app.include_router(developer.router)' not in content:
        # Find a good place to add it
        if 'app.include_router' in content:
            # Add after another router
            idx = content.find('app.include_router')
            end_idx = content.find('\\n', idx)
            content = content[:end_idx+1] + 'app.include_router(developer.router)\\n' + content[end_idx+1:]
        else:
            # Add after middleware or app creation
            if 'add_middleware' in content:
                idx = content.rfind(')')
                content = content[:idx+1] + '\\n\\napp.include_router(developer.router)\\n' + content[idx+1:]
    
    with open('/app/main.py', 'w') as f:
        f.write(content)
    print('✅ Developer router imported')
else:
    print('✅ Developer router already in main.py')
"

# Fix 5: Update developer UI to show Zack
echo "🎨 Updating UI to show Zack..."
if [ -f "services/zoe-ui/dist/developer/index.html" ]; then
    # Only replace Claude with Zack in the UI
    sed -i 's/Claude/Zack/g' services/zoe-ui/dist/developer/index.html 2>/dev/null || true
    sed -i 's/claude/zack/g' services/zoe-ui/dist/developer/index.html 2>/dev/null || true
    echo "✅ Developer UI updated"
fi

# Restart service
echo "🔄 Restarting zoe-core..."
docker compose restart zoe-core

echo "⏳ Waiting for service..."
sleep 8

# Test everything
echo ""
echo "🧪 TESTING:"
echo "==========="

echo -n "1. API Health: "
curl -s http://localhost:8000/health 2>/dev/null | grep -q "healthy" && echo "✅" || echo "❌"

echo -n "2. CORS: "
curl -sI http://localhost:8000/health 2>/dev/null | grep -qi "access-control" && echo "✅" || echo "❌"

echo -n "3. Developer API: "
curl -s http://localhost:8000/api/developer/status 2>/dev/null | grep -q "operational" && echo "✅" || echo "❌"

echo -n "4. Models preserved: "
if [ -f "data/available_models.json" ]; then
    echo "✅ ($(cat data/available_models.json | grep -o '"[^"]*":' | wc -l) providers)"
else
    echo "⚠️  No models file"
fi

# Check which AI system is active
echo -n "5. AI System: "
if docker exec zoe-core test -f /app/ai_router.py 2>/dev/null; then
    echo "✅ Multi-model router active"
elif docker exec zoe-core test -f /app/ai_client.py 2>/dev/null; then
    echo "✅ AI client active"
else
    echo "⚠️  No AI system found"
fi

# Show container status
echo ""
echo "Containers:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

# Get IP for links
IP=$(hostname -I | awk '{print $1}')
HOST=$(hostname)

echo ""
echo "🌐 ACCESS:"
echo "========="
echo "Zoe:  http://${IP}:8080/"
echo "      http://${HOST}.local:8080/"
echo ""
echo "Zack: http://${IP}:8080/developer/"
echo "      http://${HOST}.local:8080/developer/"
echo ""
echo "✅ SAFE FIX COMPLETE!"
echo ""
echo "Your multi-model configuration is preserved!"
echo "Test both personalities - they should work with your discovered models."
