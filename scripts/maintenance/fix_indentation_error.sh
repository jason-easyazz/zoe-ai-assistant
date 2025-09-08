#!/bin/bash
# FIX_INDENTATION_ERROR.sh
# Location: scripts/maintenance/fix_indentation_error.sh
# Purpose: Fix the indentation error at line 372 in developer_tasks.py

set -e

echo "🔧 Fixing IndentationError in developer_tasks.py"
echo "================================================"

cd /home/pi/zoe

# Step 1: Check the problematic area
echo "📄 Step 1: Checking lines around 372..."
echo "----------------------------------------"
docker exec zoe-core sed -n '370,375p' /app/routers/developer_tasks.py

# Step 2: Backup the file
echo -e "\n📦 Step 2: Creating backup..."
echo "------------------------------"
docker exec zoe-core cp /app/routers/developer_tasks.py /app/routers/developer_tasks.py.backup_indent

# Step 3: Fix the indentation error
echo -e "\n🔧 Step 3: Fixing indentation..."
echo "---------------------------------"

docker exec zoe-core python3 -c "
import re

# Read the file
with open('/app/routers/developer_tasks.py', 'r') as f:
    lines = f.readlines()

# Fix common indentation issues
fixed_lines = []
prev_indent = 0
in_function = False
in_class = False

for i, line in enumerate(lines, 1):
    # Skip empty lines
    if not line.strip():
        fixed_lines.append(line)
        continue
    
    # Get current indentation
    stripped = line.lstrip()
    current_indent = len(line) - len(stripped)
    
    # Around line 372, fix the import logging issue
    if i >= 370 and i <= 375:
        if 'import logging' in line and current_indent > 0:
            # This should be at module level (no indent)
            fixed_lines.append('import logging\\n')
            print(f'Fixed line {i}: import logging (removed indent)')
        else:
            fixed_lines.append(line)
    else:
        fixed_lines.append(line)

# Write back
with open('/app/routers/developer_tasks.py', 'w') as f:
    f.writelines(fixed_lines)

print('✅ Indentation fixed')
"

# Step 4: Alternative fix - more aggressive
echo -e "\n🔨 Step 4: Aggressive fix if needed..."
echo "---------------------------------------"

# Check if the error persists
if ! docker exec zoe-core python3 -c "import sys; sys.path.append('/app'); from routers import developer_tasks" 2>/dev/null; then
    echo "Still has errors, applying aggressive fix..."
    
    docker exec zoe-core python3 -c "
# Read the file
with open('/app/routers/developer_tasks.py', 'r') as f:
    content = f.read()

# Find and fix the problematic section
lines = content.split('\\n')

# Look for the execute_task_async function which likely has the issue
fixed_lines = []
indent_level = 0
prev_line = ''

for i, line in enumerate(lines):
    # If we find import statements with wrong indentation
    if line.strip().startswith('import ') or line.strip().startswith('from '):
        # These should typically be at the top level
        if i > 20 and len(line) - len(line.lstrip()) > 0:
            # This is likely a misindented import
            fixed_lines.append(line.lstrip())
            print(f'Fixed import at line {i+1}: {line.strip()}')
        else:
            fixed_lines.append(line)
    else:
        fixed_lines.append(line)

# Rejoin and save
content = '\\n'.join(fixed_lines)

with open('/app/routers/developer_tasks.py', 'w') as f:
    f.write(content)

print('✅ Applied aggressive indentation fix')
"
fi

# Step 5: Verify the fix
echo -e "\n✅ Step 5: Verifying the fix..."
echo "--------------------------------"

docker exec zoe-core python3 -c "
import sys
import ast
sys.path.append('/app')

# First check syntax
try:
    with open('/app/routers/developer_tasks.py', 'r') as f:
        code = f.read()
    ast.parse(code)
    print('✅ No syntax errors!')
except SyntaxError as e:
    print(f'❌ Still has syntax error at line {e.lineno}: {e.msg}')
    print(f'Text: {e.text}')
    import sys
    sys.exit(1)

# Then try to import
try:
    from routers import developer_tasks
    print('✅ Module imports successfully!')
    
    # Check router
    if hasattr(developer_tasks, 'router'):
        print(f'✅ Router exists with {len(developer_tasks.router.routes)} routes')
    else:
        print('❌ No router found')
except Exception as e:
    print(f'❌ Import error: {e}')
    import traceback
    traceback.print_exc()
"

# Step 6: Restart service
echo -e "\n🐳 Step 6: Restarting service..."
echo "---------------------------------"
docker compose restart zoe-core
sleep 10

# Step 7: Test the endpoints
echo -e "\n🧪 Step 7: Testing endpoints..."
echo "--------------------------------"

echo "Testing /api/developer/tasks/:"
curl -s http://localhost:8000/api/developer/tasks/ | jq '.' || echo "Not working"

echo -e "\nTesting /api/developer/tasks/list:"
RESPONSE=$(curl -s http://localhost:8000/api/developer/tasks/list)
if echo "$RESPONSE" | jq '.' 2>/dev/null; then
    echo "✅ Endpoints are working!"
else
    echo "Response: $RESPONSE"
fi

# Step 8: If still not working, show the exact problem
if [ "$?" -ne 0 ]; then
    echo -e "\n❌ Still not working. Checking exact issue..."
    echo "----------------------------------------------"
    docker logs zoe-core --tail 20 | grep -E "ERROR|Error|ImportError|SyntaxError"
fi

echo -e "\n📊 Summary"
echo "=========="
echo "✅ Indentation error fixed"
echo "✅ File syntax verified"
echo "✅ Service restarted"
echo ""
echo "Test the endpoints:"
echo "  curl http://localhost:8000/api/developer/tasks/list"
echo "  curl http://localhost:8000/api/developer/tasks/8d9d514a/execute -X POST"
