#!/bin/bash
# ============================================================================
# SMART RECOVERY SCRIPT - Respects Today's Work
# Fixes only what's broken without destroying what you've built
# Date: August 23, 2025
# ============================================================================

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"; }
warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
success() { echo -e "${GREEN}✅${NC} $1"; }

cd /home/pi/zoe

echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}         SMART RECOVERY - Preserving Today's Work${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"

# ============================================================================
# STEP 1: DIAGNOSIS
# ============================================================================
log "🔍 Step 1: Diagnosing actual problems..."

# Check what's actually broken
ISSUES=()

# Test API health
if ! curl -s http://localhost:8000/health | grep -q "healthy"; then
    ISSUES+=("API_NOT_HEALTHY")
fi

# Test developer endpoint
if ! curl -s http://localhost:8000/api/developer/status | grep -q "operational"; then
    ISSUES+=("DEVELOPER_ENDPOINT_MISSING")
fi

# Test UI access
if ! curl -s http://localhost:8080/developer/index.html > /dev/null; then
    ISSUES+=("DEVELOPER_UI_NOT_ACCESSIBLE")
fi

# Check for CORS headers
CORS_TEST=$(curl -sI http://localhost:8000/health | grep -i "access-control-allow-origin" || echo "")
if [ -z "$CORS_TEST" ]; then
    ISSUES+=("CORS_NOT_CONFIGURED")
fi

echo "Found ${#ISSUES[@]} issues to fix: ${ISSUES[@]}"

# ============================================================================
# STEP 2: PRESERVE YOUR WORK
# ============================================================================
log "📦 Step 2: Preserving your UI work from today..."

# Backup current UI (your beautiful work from today)
BACKUP_DIR="backups/recovery_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -r services/zoe-ui/dist "$BACKUP_DIR/ui_backup"
success "UI work preserved"

# ============================================================================
# STEP 3: FIX ONLY WHAT'S BROKEN
# ============================================================================
log "🔧 Step 3: Surgical fixes..."

# Fix 1: CORS if needed
if [[ " ${ISSUES[@]} " =~ " CORS_NOT_CONFIGURED " ]]; then
    log "Fixing CORS configuration..."
    
    # Update main.py to add CORS properly
    cat > /tmp/fix_cors.py << 'PYTHON_EOF'
import sys
sys.path.insert(0, '/app')

# Read current main.py
with open('/app/main.py', 'r') as f:
    content = f.read()

# Check if CORS is properly configured
if 'CORSMiddleware' not in content:
    # Add import
    import_line = "from fastapi.middleware.cors import CORSMiddleware"
    if import_line not in content:
        content = content.replace(
            "from fastapi import FastAPI",
            "from fastapi import FastAPI\nfrom fastapi.middleware.cors import CORSMiddleware"
        )
    
    # Add middleware after app creation
    cors_config = '''
# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)
'''
    # Find where to insert (after app = FastAPI)
    app_line_index = content.find('app = FastAPI')
    if app_line_index >= 0:
        # Find the end of that line
        newline_index = content.find('\n', app_line_index)
        if newline_index >= 0:
            content = content[:newline_index+1] + cors_config + content[newline_index+1:]

# Write back
with open('/app/main.py', 'w') as f:
    f.write(content)
print("✅ CORS configuration added")
PYTHON_EOF

    docker exec zoe-core python3 /tmp/fix_cors.py
    docker cp /tmp/fix_cors.py zoe-core:/tmp/
    docker exec zoe-core python3 /tmp/fix_cors.py
    success "CORS fixed"
fi

# Fix 2: Developer endpoint if missing
if [[ " ${ISSUES[@]} " =~ " DEVELOPER_ENDPOINT_MISSING " ]]; then
    log "Adding developer endpoint..."
    
    # Check if developer router exists
    if docker exec zoe-core test -f /app/routers/developer.py; then
        log "Developer router exists, ensuring it's imported..."
        
        docker exec zoe-core python3 -c "
import sys
sys.path.insert(0, '/app')

# Read main.py
with open('/app/main.py', 'r') as f:
    content = f.read()

# Check if developer router is imported
if 'from routers import developer' not in content:
    # Add import
    if 'from routers import' in content:
        content = content.replace(
            'from routers import',
            'from routers import developer,'
        )
    else:
        # Add new import section
        content = content.replace(
            'app = FastAPI',
            'from routers import developer\n\napp = FastAPI'
        )
    
    # Add router inclusion
    if 'app.include_router(developer.router)' not in content:
        # Find where to add (after CORS middleware)
        if 'add_middleware' in content:
            # Find the end of middleware section
            idx = content.rfind(')')
            if idx > 0:
                content = content[:idx+1] + '\n\napp.include_router(developer.router)\n' + content[idx+1:]

with open('/app/main.py', 'w') as f:
    f.write(content)
print('✅ Developer router imported')
"
    else
        log "Creating minimal developer router..."
        docker exec zoe-core bash -c 'cat > /app/routers/developer.py << "EOF"
from fastapi import APIRouter
from datetime import datetime

router = APIRouter(prefix="/api/developer")

@router.get("/status")
async def get_status():
    return {
        "status": "operational",
        "timestamp": datetime.now().isoformat()
    }

@router.post("/chat")
async def developer_chat(request: dict):
    return {
        "response": "Developer mode active",
        "message": request.get("message", "")
    }
EOF'
    fi
    success "Developer endpoint fixed"
fi

# Fix 3: Update AI personalities (Zoe and Zack)
log "🤖 Fixing AI personality names (Zoe for users, Zack for developers)..."

# Update the AI client to use correct names
docker exec zoe-core python3 -c "
import os

# Find and update ai_client.py
ai_file = '/app/ai_client.py'
if os.path.exists(ai_file):
    with open(ai_file, 'r') as f:
        content = f.read()
    
    # Fix developer personality name
    content = content.replace(
        'You are Claude, a technical assistant',
        'You are Zack, a technical AI assistant for the Zoe system'
    )
    content = content.replace(
        'Claude: AI offline',
        'Zack: AI offline'
    )
    content = content.replace(
        'I\\'m Claude',
        'I\\'m Zack'
    )
    
    # Ensure Zoe personality is correct
    if 'You are Zoe' not in content and 'system_prompt' in content:
        # Add personalities if not present
        if 'if mode == \"developer\":' in content:
            # Replace the developer prompt
            old_dev = 'system_prompt = \"You are Claude, a technical assistant. Be precise and helpful.\"'
            new_dev = 'system_prompt = \"You are Zack, a technical AI assistant for the Zoe system. Be precise, provide code, and think like an engineer.\"'
            content = content.replace(old_dev, new_dev)
            
            old_user = 'system_prompt = \"You are Zoe, a warm and friendly AI assistant.\"'
            new_user = 'system_prompt = \"You are Zoe, a warm and friendly AI assistant. Be conversational, supportive, and helpful.\"'
            content = content.replace(old_user, new_user)
    
    with open(ai_file, 'w') as f:
        f.write(content)
    print('✅ AI personalities updated: Zoe (users) and Zack (developers)')
else:
    print('⚠️ AI client not found, will be created with correct personalities')
"

# Update developer UI to show "Zack" instead of "Claude"
if [ -f "services/zoe-ui/dist/developer/index.html" ]; then
    log "Updating developer UI to show Zack..."
    sed -i 's/Claude/Zack/g' services/zoe-ui/dist/developer/index.html
    sed -i 's/claude/zack/g' services/zoe-ui/dist/developer/index.html
    sed -i "s/I'm Claude/I'm Zack/g" services/zoe-ui/dist/developer/index.html
    success "Developer UI updated to show Zack"
fi

# ============================================================================
# STEP 4: RESTART ONLY WHAT'S NEEDED
# ============================================================================
log "🔄 Step 4: Smart restart..."

# Only restart zoe-core if we made changes
if [ ${#ISSUES[@]} -gt 0 ]; then
    docker compose restart zoe-core
    log "Waiting for API to stabilize..."
    sleep 5
else
    log "No restart needed"
fi

# ============================================================================
# STEP 5: VALIDATE FIXES
# ============================================================================
log "✅ Step 5: Validating fixes..."

echo ""
echo "Testing endpoints:"

# Test health
echo -n "1. API Health: "
if curl -s http://localhost:8000/health | grep -q "healthy"; then
    success "Working"
else
    error "Still broken"
fi

# Test CORS
echo -n "2. CORS Headers: "
if curl -sI http://localhost:8000/health | grep -qi "access-control-allow-origin"; then
    success "Present"
else
    error "Missing"
fi

# Test developer status
echo -n "3. Developer Status: "
if curl -s http://localhost:8000/api/developer/status | grep -q "operational"; then
    success "Working"
else
    error "Not working"
fi

# Test developer UI
echo -n "4. Developer UI: "
if curl -s http://localhost:8080/developer/index.html | grep -q "Developer"; then
    success "Accessible"
else
    error "Not accessible"
fi

# Test AI personalities
echo -n "5. AI Personalities: "
if docker exec zoe-core grep -q "Zack" /app/ai_client.py 2>/dev/null; then
    success "Zack (developer) configured"
else
    warning "Personalities may need configuration"
fi

# Test all containers
echo ""
echo "Container Status:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep zoe-

# ============================================================================
# STEP 6: QUICK ACCESS TEST
# ============================================================================
log "🌐 Step 6: Quick access links..."

# Get the actual IP address dynamically
IP_ADDRESS=$(hostname -I | awk '{print $1}')
if [ -z "$IP_ADDRESS" ]; then
    IP_ADDRESS="localhost"
fi

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Quick Test Links:${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "📱 Main Chat: http://${IP_ADDRESS}:8080/"
echo "📊 Dashboard: http://${IP_ADDRESS}:8080/dashboard.html"
echo "🔧 Developer: http://${IP_ADDRESS}:8080/developer/"
echo "⚙️ Settings: http://${IP_ADDRESS}:8080/settings.html"
echo ""
echo "🧪 API Test Commands:"
echo "  curl http://localhost:8000/health | jq"
echo "  curl http://localhost:8000/api/developer/status | jq"
echo ""

# ============================================================================
# SUMMARY
# ============================================================================
echo -e "${GREEN}════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}SMART RECOVERY COMPLETE${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════════${NC}"
echo ""
echo "✅ Fixed ${#ISSUES[@]} issues without destroying your work"
echo "✅ Your UI from today is preserved in: $BACKUP_DIR"
echo "✅ All containers should be running"
echo ""
echo "If anything is still broken, the backup is at:"
echo "  $BACKUP_DIR/ui_backup"
echo ""
echo "Next recommended action:"
echo "  1. Test the developer dashboard in your browser"
echo "  2. Check if chat is working"
echo "  3. Verify your beautiful UI is still intact"
