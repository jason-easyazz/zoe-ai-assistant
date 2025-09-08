#!/bin/bash
# FIX_ENTIRE_FUNCTION.sh
# Location: scripts/maintenance/fix_entire_function.sh
# Purpose: Fix the entire execute_task_async function indentation

set -e

echo "🔧 Fixing Entire execute_task_async Function"
echo "==========================================="

cd /home/pi/zoe

# Step 1: Show the problem area
echo "📄 Step 1: Current problematic code (lines 365-385)..."
echo "------------------------------------------------------"
docker exec zoe-core sed -n '365,385p' /app/routers/developer_tasks.py

# Step 2: Replace the broken function with a properly indented one
echo -e "\n🔧 Step 2: Replacing broken function..."
echo "----------------------------------------"

docker exec zoe-core python3 << 'PYTHON_FIX'
# Read the file
with open('/app/routers/developer_tasks.py', 'r') as f:
    lines = f.readlines()

# Find the execute_task_async function
start_line = None
end_line = None

for i, line in enumerate(lines):
    if 'async def execute_task_async' in line:
        start_line = i
        # Find the end of the function (next function or end of file)
        for j in range(i+1, len(lines)):
            if j < len(lines) - 1 and (lines[j].startswith('def ') or lines[j].startswith('async def ') or lines[j].startswith('@')):
                end_line = j
                break
        if end_line is None:
            end_line = len(lines)
        break

if start_line is not None:
    print(f"Found function at lines {start_line+1} to {end_line}")
    
    # Replace with properly indented version
    new_function = '''async def execute_task_async(task_id: str, execution_id: int, plan: dict):
    """Background task execution with adaptation"""
    from .task_executor import TaskExecutor
    from .plan_generator import PlanGenerator
    import logging
    
    logger = logging.getLogger(__name__)
    executor = TaskExecutor(task_id)
    
    try:
        # Execute the task with full tracking
        result = await executor.execute_task(task_id, plan)
        
        logger.info(f"Task {task_id} execution completed: {result.get('status', 'unknown')}")
        
        # Update task status based on result
        conn = sqlite3.connect("/app/data/developer_tasks.db")
        cursor = conn.cursor()
        
        if result.get("status") == "completed":
            cursor.execute("""
                UPDATE dynamic_tasks 
                SET status = 'completed', 
                    last_executed_at = CURRENT_TIMESTAMP,
                    execution_count = execution_count + 1
                WHERE id = ?
            """, (task_id,))
        else:
            cursor.execute("""
                UPDATE dynamic_tasks 
                SET status = 'failed',
                    last_executed_at = CURRENT_TIMESTAMP,
                    execution_count = execution_count + 1
                WHERE id = ?
            """, (task_id,))
        
        conn.commit()
        conn.close()
        
        return result
        
    except Exception as e:
        logger.error(f"Task execution failed: {str(e)}")
        
        # Update status to failed
        conn = sqlite3.connect("/app/data/developer_tasks.db")
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE dynamic_tasks 
            SET status = 'failed',
                last_executed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (task_id,))
        conn.commit()
        conn.close()
        
        return {"status": "failed", "error": str(e)}

'''
    
    # Replace the function
    new_lines = lines[:start_line] + [new_function] + lines[end_line:]
    
    # Write back
    with open('/app/routers/developer_tasks.py', 'w') as f:
        f.writelines(new_lines)
    
    print("✅ Replaced execute_task_async with properly indented version")
else:
    print("❌ Could not find execute_task_async function")
PYTHON_FIX

# Step 3: Verify no syntax errors
echo -e "\n✅ Step 3: Verifying syntax..."
echo "-------------------------------"

docker exec zoe-core python3 -c "
import ast
try:
    with open('/app/routers/developer_tasks.py', 'r') as f:
        code = f.read()
    ast.parse(code)
    print('✅ No syntax errors!')
except SyntaxError as e:
    print(f'❌ Syntax error at line {e.lineno}: {e.msg}')
    print(f'Near: {e.text}')
    exit(1)
"

# Step 4: Test import
echo -e "\n🐍 Step 4: Testing import..."
echo "-----------------------------"

docker exec zoe-core python3 -c "
import sys
sys.path.append('/app')
try:
    from routers import developer_tasks
    print('✅ Module imports successfully!')
    print(f'Router has {len(developer_tasks.router.routes)} routes')
    
    # List routes
    for route in developer_tasks.router.routes[:5]:
        if hasattr(route, 'path'):
            print(f'  - {route.path}')
except Exception as e:
    print(f'❌ Import failed: {e}')
    import traceback
    traceback.print_exc()
"

# Step 5: Restart service
echo -e "\n🐳 Step 5: Restarting service..."
echo "---------------------------------"
docker compose restart zoe-core
sleep 10

# Step 6: Test ALL endpoints
echo -e "\n🧪 Step 6: Testing ALL endpoints..."
echo "------------------------------------"

echo "1. System info (/api/developer/tasks/):"
curl -s http://localhost:8000/api/developer/tasks/ | jq '.' || echo "Failed"

echo -e "\n2. List tasks (/api/developer/tasks/list):"
curl -s http://localhost:8000/api/developer/tasks/list | jq '.' || echo "Failed"

echo -e "\n3. Create test task:"
TASK_RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/tasks/create \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Function Fix Test",
    "objective": "Test after fixing indentation",
    "requirements": ["Create file /tmp/indent_fixed.txt with SUCCESS"],
    "constraints": [],
    "acceptance_criteria": ["File exists"],
    "priority": "low"
  }')

echo "$TASK_RESPONSE" | jq '.' || echo "$TASK_RESPONSE"

TASK_ID=$(echo "$TASK_RESPONSE" | jq -r '.task_id' 2>/dev/null)

if [ ! -z "$TASK_ID" ] && [ "$TASK_ID" != "null" ]; then
    echo -e "\n4. Execute task $TASK_ID:"
    curl -s -X POST http://localhost:8000/api/developer/tasks/$TASK_ID/execute | jq '.'
    
    sleep 5
    
    echo -e "\n5. Check file creation:"
    docker exec zoe-core ls -la /tmp/indent_fixed.txt 2>/dev/null && echo "✅ FILE CREATED!" || echo "No file yet"
fi

# Step 7: Test original task
echo -e "\n🔄 Step 7: Testing original task 8d9d514a..."
echo "----------------------------------------------"
curl -s -X POST http://localhost:8000/api/developer/tasks/8d9d514a/execute | jq '.'

sleep 3
docker exec zoe-core ls -la /tmp/test_execution.txt 2>/dev/null && echo "✅ ORIGINAL TASK WORKS!" || echo "Original task file not created"

echo -e "\n✅ Complete!"
echo "==========="
echo "The task execution system should now be fully functional!"
echo ""
echo "Test commands:"
echo "  curl http://localhost:8000/api/developer/tasks/list"
echo "  curl -X POST http://localhost:8000/api/developer/tasks/8d9d514a/execute"
