#!/bin/bash
# VERIFY_TASK_INTEGRATION.sh
# Location: scripts/testing/verify_task_integration.sh
# Purpose: Verify the complete task execution pipeline works

set -e

echo "🔍 Verifying Complete Task Integration"
echo "======================================"

cd /home/pi/zoe

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Check the current integration
echo -e "\n📋 Step 1: Checking execute_task_async integration..."
echo "------------------------------------------------------"

docker exec zoe-core python3 -c "
import sys
sys.path.append('/app')

# Check if execute_task_async properly uses TaskExecutor
try:
    with open('/app/routers/developer_tasks.py', 'r') as f:
        content = f.read()
        
    if 'TaskExecutor(' in content:
        print('✅ TaskExecutor is integrated in developer_tasks.py')
    else:
        print('⚠️  TaskExecutor not found in execute_task_async')
        print('Need to update execute_task_async to use TaskExecutor')
        
    if 'PlanGenerator(' in content:
        print('✅ PlanGenerator is integrated')
    else:
        print('⚠️  PlanGenerator not found')
        
except Exception as e:
    print(f'Error checking integration: {e}')
"

# Step 2: Create a comprehensive test task
echo -e "\n🧪 Step 2: Creating comprehensive test task..."
echo "-----------------------------------------------"

TASK_RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/tasks/create \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Integration Test",
    "objective": "Verify complete task execution pipeline",
    "requirements": [
      "Create test file /tmp/test_execution.txt with content",
      "Create file /tmp/integration_test.txt",
      "Execute command to list /tmp files"
    ],
    "constraints": ["Only work in /tmp directory"],
    "acceptance_criteria": ["Both files exist", "Command executes successfully"],
    "priority": "low"
  }')

TEST_ID=$(echo "$TASK_RESPONSE" | jq -r '.task_id')
echo "Created test task: $TEST_ID"

# Step 3: Analyze the task to see the plan
echo -e "\n📊 Step 3: Analyzing task to see generated plan..."
echo "--------------------------------------------------"
PLAN=$(curl -s -X POST http://localhost:8000/api/developer/tasks/$TEST_ID/analyze)
echo "Generated plan:"
echo "$PLAN" | jq '.steps'

# Step 4: Execute the task
echo -e "\n▶️ Step 4: Executing task..."
echo "-----------------------------"
EXEC_RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/tasks/$TEST_ID/execute)
echo "$EXEC_RESPONSE" | jq '.'

# Wait for execution
sleep 5

# Step 5: Check execution results
echo -e "\n📊 Step 5: Checking execution results..."
echo "-----------------------------------------"

# Check task status
echo "Task status:"
curl -s http://localhost:8000/api/developer/tasks/list | jq ".tasks[] | select(.id==\"$TEST_ID\") | {id, title, status, execution_count}"

# Check execution history
echo -e "\nExecution history:"
curl -s http://localhost:8000/api/developer/tasks/$TEST_ID/history | jq '.executions[0]'

# Step 6: Verify files were created
echo -e "\n📂 Step 6: Verifying files were created..."
echo "-------------------------------------------"

echo "Checking for test_execution.txt:"
docker exec zoe-core ls -la /tmp/test_execution.txt 2>/dev/null && echo -e "${GREEN}✅ test_execution.txt exists!${NC}" || echo -e "${RED}❌ test_execution.txt missing${NC}"

echo -e "\nChecking for integration_test.txt:"
docker exec zoe-core ls -la /tmp/integration_test.txt 2>/dev/null && echo -e "${GREEN}✅ integration_test.txt exists!${NC}" || echo -e "${RED}❌ integration_test.txt missing${NC}"

echo -e "\nAll files in /tmp:"
docker exec zoe-core ls -la /tmp/ | grep -E "\.txt$" || echo "No .txt files found"

# Step 7: Check if execute_task_async needs fixing
echo -e "\n🔧 Step 7: Checking if execute_task_async needs update..."
echo "-----------------------------------------------------------"

# Check the actual function
docker exec zoe-core grep -A 20 "async def execute_task_async" /app/routers/developer_tasks.py | head -25

# Step 8: If needed, patch execute_task_async
echo -e "\n🩹 Step 8: Patching execute_task_async if needed..."
echo "----------------------------------------------------"

cat > /tmp/patch_execute.py << 'EOF'
import re

with open('services/zoe-core/routers/developer_tasks.py', 'r') as f:
    content = f.read()

# Check if TaskExecutor is already being used
if 'executor = TaskExecutor(' not in content:
    print("Patching execute_task_async to use TaskExecutor...")
    
    # Find the execute_task_async function
    pattern = r'(async def execute_task_async.*?:\n)(.*?)(\n(?:async )?def |\Z)'
    
    # New implementation
    new_impl = '''async def execute_task_async(task_id: str, execution_id: int, plan: dict):
    """Execute task in background using TaskExecutor"""
    try:
        from routers.task_executor import TaskExecutor
        from routers.plan_generator import PlanGenerator
        
        # Get task details
        conn = sqlite3.connect("/app/data/developer_tasks.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dynamic_tasks WHERE id = ?", (task_id,))
        task = cursor.fetchone()
        conn.close()
        
        if not task:
            print(f"Task {task_id} not found")
            return
        
        # Generate fresh plan
        generator = PlanGenerator()
        task_data = {
            'task_id': task_id,
            'objective': task[2],  # objective column
            'requirements': json.loads(task[3]),  # requirements
            'constraints': json.loads(task[4]) if task[4] else [],
            'context': {}
        }
        
        fresh_plan = generator.generate_plan(task_data)
        
        # Execute using TaskExecutor
        executor = TaskExecutor(task_id)
        result = executor.execute_plan(fresh_plan)
        
        # Update task status based on result
        status = 'completed' if result.get('success') else 'failed'
        conn = sqlite3.connect("/app/data/developer_tasks.db")
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE dynamic_tasks 
            SET status = ?, last_executed_at = CURRENT_TIMESTAMP, execution_count = execution_count + 1
            WHERE id = ?
        """, (status, task_id))
        conn.commit()
        conn.close()
        
        print(f"Task {task_id} execution {'succeeded' if result.get('success') else 'failed'}")
        
    except Exception as e:
        print(f"Error executing task {task_id}: {e}")
        import traceback
        traceback.print_exc()
'''
    
    # Replace the function
    match = re.search(pattern, content, re.DOTALL)
    if match:
        # Keep everything before the function, add new implementation, keep everything after
        before_func = content[:content.index('async def execute_task_async')]
        after_func_match = re.search(r'\n(?:async )?def (?!execute_task_async)', 
                                     content[content.index('async def execute_task_async'):])
        if after_func_match:
            after_func = content[content.index('async def execute_task_async') + after_func_match.start():]
        else:
            after_func = ""
        
        content = before_func + new_impl + "\n" + after_func
        
        with open('services/zoe-core/routers/developer_tasks.py', 'w') as f:
            f.write(content)
        
        print("✅ Patched execute_task_async to use TaskExecutor")
    else:
        print("Could not find execute_task_async function")
else:
    print("✅ execute_task_async already uses TaskExecutor")
EOF

python3 /tmp/patch_execute.py

# Restart if we patched
if grep -q "Patched execute_task_async" /tmp/patch_execute.py; then
    echo "Restarting zoe-core after patch..."
    docker compose restart zoe-core
    sleep 8
fi

# Step 9: Final test with the original task
echo -e "\n🔄 Step 9: Final test with original task 8d9d514a..."
echo "------------------------------------------------------"

# Re-execute the original test task
curl -s -X POST http://localhost:8000/api/developer/tasks/8d9d514a/execute | jq '.'

sleep 5

# Check if it finally worked
echo -e "\nFinal check for test_execution.txt:"
docker exec zoe-core ls -la /tmp/test_execution.txt 2>/dev/null && echo -e "${GREEN}✅ SUCCESS! Original task now works!${NC}" || echo -e "${YELLOW}Original task still needs investigation${NC}"

# Step 10: Summary
echo -e "\n📊 Summary"
echo "=========="

TOTAL_TASKS=$(curl -s http://localhost:8000/api/developer/tasks/list | jq '.tasks | length')
COMPLETED=$(curl -s http://localhost:8000/api/developer/tasks/list | jq '[.tasks[] | select(.status == "completed")] | length')
FAILED=$(curl -s http://localhost:8000/api/developer/tasks/list | jq '[.tasks[] | select(.status == "failed")] | length')
PENDING=$(curl -s http://localhost:8000/api/developer/tasks/list | jq '[.tasks[] | select(.status == "pending")] | length')

echo "Total tasks: $TOTAL_TASKS"
echo "Completed: $COMPLETED"
echo "Failed: $FAILED"
echo "Pending: $PENDING"

echo -e "\n✅ Integration verification complete!"
echo ""
echo "Next steps:"
echo "  1. If files are being created, the system works!"
echo "  2. Execute real tasks like Redis caching (ff84a566)"
echo "  3. Build the Task Management UI (df03a0fb)"
echo "  4. Monitor with: docker logs -f zoe-core"
