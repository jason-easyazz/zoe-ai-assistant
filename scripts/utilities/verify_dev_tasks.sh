#!/bin/bash
# VERIFY_DEV_TASKS.sh
# Purpose: Verify and fix the developer tasks registration

set -e

echo "🔍 Verifying Developer Tasks Registration"
echo "========================================"
echo ""

cd /home/pi/zoe

# Step 1: Check if developer_tasks.py exists
echo "📄 Checking developer_tasks.py..."
if docker exec zoe-core test -f /app/routers/developer_tasks.py; then
    echo "✓ developer_tasks.py exists"
    LINE_COUNT=$(docker exec zoe-core wc -l /app/routers/developer_tasks.py | cut -d' ' -f1)
    echo "  Lines: $LINE_COUNT"
else
    echo "✗ developer_tasks.py NOT FOUND!"
fi

# Step 2: Check main.py imports
echo -e "\n📦 Checking main.py imports..."
docker exec zoe-core grep "from routers import" /app/main.py || echo "No routers import found!"

# Step 3: Check router registrations
echo -e "\n🔌 Checking router registrations..."
docker exec zoe-core grep "app.include_router" /app/main.py | grep -v "^#" || echo "No router registrations found!"

# Step 4: Check if developer_tasks is imported and included
echo -e "\n🔍 Specific checks:"
if docker exec zoe-core grep -q "developer_tasks" /app/main.py; then
    echo "✓ developer_tasks mentioned in main.py"
else
    echo "✗ developer_tasks NOT in main.py!"
fi

# Step 5: Fix the registration
echo -e "\n🔧 Fixing registration..."
docker exec zoe-core python3 << 'PYTHON'
import sys
import os

# Read main.py
with open('/app/main.py', 'r') as f:
    lines = f.readlines()

# Check current state
has_import = False
has_include = False

for line in lines:
    if 'from routers import' in line and 'developer_tasks' in line:
        has_import = True
    if 'app.include_router(developer_tasks.router)' in line:
        has_include = True

print(f"Current state: import={has_import}, include={has_include}")

# Fix if needed
modified = False

# Fix import
if not has_import:
    for i, line in enumerate(lines):
        if 'from routers import' in line:
            # Add developer_tasks to the import
            if line.strip().endswith('developer'):
                lines[i] = line.rstrip() + ', developer_tasks\n'
            elif 'aider' in line:
                lines[i] = line.replace('aider', 'aider, developer_tasks')
            else:
                lines[i] = line.rstrip().rstrip(',') + ', developer_tasks\n'
            modified = True
            print("✓ Added developer_tasks to imports")
            break

# Fix include
if not has_include:
    for i, line in enumerate(lines):
        if 'app.include_router(developer.router)' in line:
            # Add after developer router
            indent = len(line) - len(line.lstrip())
            new_line = ' ' * indent + 'app.include_router(developer_tasks.router)\n'
            lines.insert(i + 1, new_line)
            modified = True
            print("✓ Added developer_tasks router inclusion")
            break

# Write back if modified
if modified:
    with open('/app/main.py', 'w') as f:
        f.writelines(lines)
    print("✓ main.py updated successfully")
else:
    print("✓ main.py already correct")

# Show the result
print("\nFinal configuration:")
with open('/app/main.py', 'r') as f:
    for line in f:
        if 'from routers import' in line:
            print(f"Import: {line.strip()}")
        if 'developer' in line and 'app.include_router' in line:
            print(f"Include: {line.strip()}")
PYTHON

# Step 6: Test the import
echo -e "\n🐍 Testing import..."
docker exec zoe-core python3 << 'PYTHON'
try:
    import sys
    sys.path.append('/app')
    from routers import developer_tasks
    print("✓ developer_tasks imports successfully")
    
    # Check router
    if hasattr(developer_tasks, 'router'):
        print(f"✓ Router exists with prefix: {developer_tasks.router.prefix}")
        print(f"✓ Number of routes: {len(developer_tasks.router.routes)}")
        
        # List routes
        print("\nAvailable routes:")
        for route in developer_tasks.router.routes:
            if hasattr(route, 'path'):
                print(f"  {route.methods} {developer_tasks.router.prefix}{route.path}")
    else:
        print("✗ No router found!")
        
except Exception as e:
    print(f"✗ Import error: {e}")
    import traceback
    traceback.print_exc()
PYTHON

# Step 7: Restart service
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core

echo "⏳ Waiting for service..."
sleep 8

# Step 8: Final test
echo -e "\n✅ Final Test..."

# Test the info endpoint
echo "Testing developer tasks info endpoint..."
INFO=$(curl -s http://localhost:8000/api/developer/tasks/ 2>/dev/null || echo "{}")

if echo "$INFO" | grep -q "Developer Task Management"; then
    echo "✅ DEVELOPER TASKS SYSTEM IS WORKING!"
    echo "$INFO" | jq '.'
    
    # Try creating a task
    echo -e "\nCreating a test task..."
    ./scripts/utilities/dev_tasks.sh create "Test Task" "Testing the system" || true
    
    # List tasks
    echo -e "\nListing tasks..."
    ./scripts/utilities/dev_tasks.sh list || true
    
else
    echo "⚠️ Still not working. Checking all endpoints..."
    curl -s http://localhost:8000/openapi.json | jq '.paths | keys[]' | grep developer || echo "No developer endpoints found"
    
    echo -e "\nChecking service logs..."
    docker logs zoe-core --tail 30 | grep -E "error|Error|ERROR|developer_tasks" || true
fi

echo -e "\n📊 VERIFICATION COMPLETE"
echo "========================"
if curl -s http://localhost:8000/api/developer/tasks/ 2>/dev/null | grep -q "Developer Task Management"; then
    echo "✅ Developer Tasks System is ACTIVE"
    echo ""
    echo "Test it:"
    echo "  ./scripts/utilities/dev_tasks.sh info"
    echo "  ./scripts/utilities/dev_tasks.sh create \"My Task\" \"Description\""
    echo "  ./scripts/utilities/dev_tasks.sh list"
else
    echo "⚠️ System needs attention"
    echo ""
    echo "Debug with:"
    echo "  docker logs zoe-core --tail 50"
    echo "  docker exec zoe-core cat /app/main.py | grep developer"
fi
