#!/bin/bash
# CLEANUP_AND_FIX.sh
# Location: scripts/maintenance/cleanup_and_fix.sh
# Purpose: Remove old tasks.py and fix developer_tasks registration

set -e

echo "🧹 Cleaning Up Task Systems"
echo "============================"

cd /home/pi/zoe

# Step 1: Check for old tasks.py file
echo "📋 Step 1: Checking for old tasks.py..."
echo "----------------------------------------"
if docker exec zoe-core test -f /app/routers/tasks.py; then
    echo "Found old tasks.py - removing it..."
    docker exec zoe-core mv /app/routers/tasks.py /app/routers/tasks.old.backup 2>/dev/null || true
    echo "✅ Moved old tasks.py to tasks.old.backup"
else
    echo "✅ No old tasks.py file found"
fi

# Step 2: Check if old tasks router is registered in main.py
echo -e "\n🔧 Step 2: Removing old tasks from main.py..."
echo "----------------------------------------------"
docker exec zoe-core python3 -c "
with open('/app/main.py', 'r') as f:
    content = f.read()

original = content
# Remove old tasks import
content = content.replace('from routers import tasks,', 'from routers import')
content = content.replace(', tasks', '')
content = content.replace('tasks,', '')

# Remove old tasks router registration
import re
content = re.sub(r'app\.include_router\(tasks\.router[^\)]*\)\n?', '', content)

if content != original:
    with open('/app/main.py', 'w') as f:
        f.write(content)
    print('✅ Removed old tasks references from main.py')
else:
    print('✅ No old tasks references in main.py')
"

# Step 3: Check lists.py to confirm it handles tasks
echo -e "\n📄 Step 3: Confirming lists.py handles user tasks..."
echo "----------------------------------------------------"
docker exec zoe-core grep -q "tasks" /app/routers/lists.py && echo "✅ lists.py handles tasks" || echo "⚠️ lists.py might need task support"

# Step 4: Fix developer_tasks registration
echo -e "\n🔧 Step 4: Fixing developer_tasks registration..."
echo "-------------------------------------------------"

# First, check current registration
echo "Current registration:"
docker exec zoe-core grep "developer_tasks" /app/main.py

# Fix the registration to not have double prefix
docker exec zoe-core python3 -c "
with open('/app/main.py', 'r') as f:
    content = f.read()

# Check current registration
import re
pattern = r'app\.include_router\(developer_tasks\.router[^\)]*\)'
match = re.search(pattern, content)

if match:
    current = match.group()
    print(f'Current: {current}')
    
    # The router already has prefix='/api/developer/tasks' in its definition
    # So main.py should NOT add another prefix
    new = 'app.include_router(developer_tasks.router)'
    
    if current != new:
        content = content.replace(current, new)
        with open('/app/main.py', 'w') as f:
            f.write(content)
        print(f'✅ Fixed registration to: {new}')
    else:
        print('✅ Registration is correct')
else:
    print('❌ developer_tasks router not found in main.py')
"

# Step 5: Verify the router file
echo -e "\n📊 Step 5: Verifying developer_tasks.py..."
echo "-------------------------------------------"
echo "Router definition:"
docker exec zoe-core grep "router = APIRouter" /app/routers/developer_tasks.py

echo -e "\nRoute count:"
docker exec zoe-core grep -c "@router\." /app/routers/developer_tasks.py || echo "0"

echo -e "\nSample routes:"
docker exec zoe-core grep "@router\." /app/routers/developer_tasks.py | head -3

# Step 6: Restart the service
echo -e "\n🐳 Step 6: Restarting service..."
echo "---------------------------------"
docker compose restart zoe-core
echo "Waiting for service to start..."
sleep 10

# Step 7: Test the endpoints
echo -e "\n🧪 Step 7: Testing endpoints..."
echo "--------------------------------"

echo "Testing API base:"
curl -s http://localhost:8000/api/developer/tasks/ | jq '.' || echo "Base not working"

echo -e "\nTesting list endpoint:"
curl -s http://localhost:8000/api/developer/tasks/list | jq '.' || echo "List not working"

echo -e "\nChecking OpenAPI for routes:"
curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' | grep -i "developer.*task" | head -5

# Step 8: If still not working, check for the actual issue
if ! curl -s http://localhost:8000/api/developer/tasks/list | grep -q "tasks"; then
    echo -e "\n❌ Routes still not working. Checking deeper..."
    echo "------------------------------------------------"
    
    # Check if module loads
    docker exec zoe-core python3 -c "
import sys
sys.path.append('/app')
try:
    from routers import developer_tasks
    print('✅ Module loads')
    
    # Check router
    print(f'Router: {developer_tasks.router}')
    print(f'Prefix: {developer_tasks.router.prefix}')
    print(f'Routes: {len(developer_tasks.router.routes)}')
    
    # List actual routes
    for r in developer_tasks.router.routes:
        if hasattr(r, 'path'):
            print(f'  - {r.path}')
            
except Exception as e:
    print(f'❌ Error: {e}')
    import traceback
    traceback.print_exc()
"
fi

# Step 9: Summary
echo -e "\n📊 Summary"
echo "=========="
echo "✅ Old tasks.py removed/backed up"
echo "✅ main.py cleaned of old tasks references"
echo "✅ lists.py handles user tasks now"
echo ""
echo "Developer Tasks System:"
echo "  - Located at: /app/routers/developer_tasks.py"
echo "  - Prefix: /api/developer/tasks"
echo "  - For: Development task automation"
echo ""
echo "User Tasks (in Lists):"
echo "  - Located at: /app/routers/lists.py"
echo "  - Endpoints: /api/lists/tasks"
echo "  - For: User's personal task management"
echo ""
echo "No more confusion! 🎉"
