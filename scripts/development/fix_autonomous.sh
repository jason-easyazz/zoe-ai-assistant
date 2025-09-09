#!/bin/bash
# Diagnostic and fix script for the autonomous system

echo "🔍 DIAGNOSING AUTONOMOUS SYSTEM"
echo "================================"

# 1. Check if the autonomous.py file was actually copied to container
echo "1️⃣ Checking if autonomous.py exists in container..."
docker exec zoe-core ls -la /app/routers/ | grep autonomous || echo "❌ autonomous.py NOT in container"

# 2. Check if main.py has the router import
echo ""
echo "2️⃣ Checking if main.py imports autonomous router..."
docker exec zoe-core grep -n "autonomous" /app/main.py | head -5 || echo "❌ No autonomous import found"

# 3. Check the actual developer dashboard endpoint
echo ""
echo "3️⃣ Testing basic developer dashboard..."
curl -s http://localhost:8000/api/developer/dashboard | jq '.'

# 4. Check container logs for errors
echo ""
echo "4️⃣ Checking for errors in zoe-core..."
docker logs zoe-core --tail 20 | grep -i error || echo "No recent errors"

# NOW LET'S FIX IT
echo ""
echo "🔧 APPLYING COMPREHENSIVE FIX"
echo "=============================="

# Fix 1: Ensure the routers directory exists in container
echo "📁 Creating routers directory in container..."
docker exec zoe-core mkdir -p /app/routers

# Fix 2: Copy the autonomous.py directly into the container
echo "📝 Copying autonomous.py to container..."
docker cp services/zoe-core/routers/autonomous.py zoe-core:/app/routers/autonomous.py

# Fix 3: Create a simpler __init__.py for the routers
echo "📦 Creating routers __init__.py..."
cat > /tmp/routers_init.py << 'INIT_EOF'
# Routers module initialization
INIT_EOF
docker cp /tmp/routers_init.py zoe-core:/app/routers/__init__.py

# Fix 4: Update main.py IN THE CONTAINER to properly import
echo "🔄 Updating main.py in container..."
docker exec zoe-core python3 -c "
import sys
# Read current main.py
with open('/app/main.py', 'r') as f:
    content = f.read()

# Check if autonomous router is already imported
if 'from routers import autonomous' not in content:
    print('Adding autonomous router import...')
    
    # Add import after other imports
    import_section = '''
# Import autonomous router
try:
    from routers import autonomous
    print('✅ Autonomous router loaded')
except Exception as e:
    print(f'❌ Could not load autonomous router: {e}')
    autonomous = None
'''
    
    # Find where to insert (after the imports)
    insert_pos = content.find('app = FastAPI')
    if insert_pos > 0:
        content = content[:insert_pos] + import_section + content[insert_pos:]
    
    # Add router inclusion after app creation
    if 'app.include_router(autonomous.router)' not in content:
        app_section = '''
# Include autonomous router if available
if autonomous:
    app.include_router(autonomous.router)
    print('✅ Autonomous endpoints registered')
'''
        # Find where to add (after FastAPI app creation)
        insert_pos = content.find('app.add_middleware')
        if insert_pos > 0:
            content = content[:insert_pos] + app_section + content[insert_pos:]
    
    # Write back
    with open('/app/main.py', 'w') as f:
        f.write(content)
    print('✅ main.py updated')
else:
    print('Autonomous router already imported')
"

# Fix 5: Restart the container to load changes
echo "🔄 Restarting zoe-core..."
docker restart zoe-core

echo "⏳ Waiting for container to start..."
sleep 10

# Fix 6: Test the endpoints again
echo ""
echo "🧪 TESTING FIXED ENDPOINTS"
echo "=========================="

echo "📊 System Overview:"
curl -s http://localhost:8000/api/developer/system/overview 2>/dev/null | jq 'keys' || echo "Still building..."

echo ""
echo "📋 Tasks Endpoint:"
curl -s http://localhost:8000/api/developer/tasks 2>/dev/null | jq '.tasks | length' || echo "Still building..."

echo ""
echo "🏥 Diagnostics:"
curl -s http://localhost:8000/api/developer/system/diagnostics 2>/dev/null | jq '.checks' || echo "Still building..."

echo ""
echo "🎯 Developer Dashboard:"
curl -s http://localhost:8000/api/developer/dashboard 2>/dev/null | jq '.'

# Check if developer page exists
echo ""
echo "🌐 Checking Developer UI..."
if [ -f "services/zoe-ui/dist/developer/index.html" ]; then
    echo "✅ Developer UI exists at: http://192.168.1.60:8080/developer/"
    echo "   File size: $(wc -l services/zoe-ui/dist/developer/index.html | cut -d' ' -f1) lines"
else
    echo "❌ Developer UI missing - need to restore it"
fi

echo ""
echo "📝 FINAL STATUS:"
echo "================"
echo "If endpoints still return null, run this command:"
echo "  docker logs zoe-core --tail 50"
echo ""
echo "To manually check what's in the container:"
echo "  docker exec -it zoe-core /bin/bash"
echo "  ls -la /app/routers/"
echo "  python3 -c 'from routers import autonomous; print(\"Loaded!\")'"
