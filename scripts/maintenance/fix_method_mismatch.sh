#!/bin/bash
# FIX_METHOD_MISMATCH.sh
# Location: scripts/maintenance/fix_method_mismatch.sh
# Purpose: Fix the method name mismatch between execute_task_async and TaskExecutor

set -e

echo "🔧 Fixing TaskExecutor Method Mismatch"
echo "======================================"

cd /home/pi/zoe

# Step 1: Show the current problem
echo "📊 Step 1: Identifying the problem..."
echo "--------------------------------------"
echo "execute_task_async calls: executor.execute_task()"
echo "TaskExecutor has method: executor.execute_plan()"
echo "Also missing task_id parameter!"

# Step 2: Fix the issue - Add execute_task method to TaskExecutor
echo -e "\n🔧 Step 2: Adding execute_task method to TaskExecutor..."
cat > /tmp/add_execute_task.py << 'EOF'
# Read the TaskExecutor file
with open('services/zoe-core/routers/task_executor.py', 'r') as f:
    content = f.read()

# Check if execute_task method already exists
if 'async def execute_task' not in content and 'def execute_task' not in content:
    # Add the execute_task method that wraps execute_plan
    additional_method = '''
    async def execute_task(self, task_id: str, plan: dict) -> dict:
        """Async wrapper for execute_plan to match the interface expected by execute_task_async"""
        # Store the task_id if not already set
        if not self.task_id:
            self.task_id = task_id
        
        # Call the synchronous execute_plan
        result = self.execute_plan(plan)
        
        # Return in the expected format
        return {
            'status': 'completed' if result.get('success') else 'failed',
            'success': result.get('success', False),
            'changes': result.get('changes', []),
            'log': result.get('log', []),
            'error': result.get('error')
        }
'''
    
    # Insert before the last line of the class
    # Find the end of the TaskExecutor class
    lines = content.split('\n')
    class_indent_found = False
    insert_index = -1
    
    for i, line in enumerate(lines):
        if 'class TaskExecutor:' in line:
            class_indent_found = True
        elif class_indent_found and line and not line[0].isspace() and 'class' in line:
            # Found next class, insert before
            insert_index = i - 1
            break
    
    if insert_index == -1:
        # Add at the end of file
        content = content.rstrip() + '\n' + additional_method + '\n'
    else:
        lines.insert(insert_index, additional_method)
        content = '\n'.join(lines)
    
    # Write back
    with open('services/zoe-core/routers/task_executor.py', 'w') as f:
        f.write(content)
    
    print("✅ Added execute_task method to TaskExecutor")
else:
    print("✅ execute_task method already exists")
EOF

python3 /tmp/add_execute_task.py

# Step 3: Fix execute_task_async to properly instantiate TaskExecutor
echo -e "\n🔧 Step 3: Fixing execute_task_async instantiation..."
cat > /tmp/fix_instantiation.py << 'EOF'
with open('services/zoe-core/routers/developer_tasks.py', 'r') as f:
    content = f.read()

# Fix the instantiation - TaskExecutor needs task_id
old_line = "    executor = TaskExecutor()"
new_line = "    executor = TaskExecutor(task_id)"

if old_line in content:
    content = content.replace(old_line, new_line)
    with open('services/zoe-core/routers/developer_tasks.py', 'w') as f:
        f.write(content)
    print("✅ Fixed TaskExecutor instantiation with task_id")
else:
    print("ℹ️ TaskExecutor instantiation already includes task_id or uses different pattern")

# Also ensure PlanGenerator is imported and used if needed
if 'from .plan_generator import PlanGenerator' not in content:
    # Add import
    import_line = 'from .plan_generator import PlanGenerator\n'
    lines = content.split('\n')
    
    # Find where to add the import (after other imports)
    for i, line in enumerate(lines):
        if 'from .task_executor import TaskExecutor' in line:
            lines.insert(i + 1, 'from .plan_generator import PlanGenerator')
            break
    
    content = '\n'.join(lines)
    with open('services/zoe-core/routers/developer_tasks.py', 'w') as f:
        f.write(content)
    print("✅ Added PlanGenerator import")
EOF

python3 /tmp/fix_instantiation.py

# Step 4: Verify the changes
echo -e "\n📄 Step 4: Verifying changes..."
echo "--------------------------------"

echo "Checking TaskExecutor methods:"
docker exec zoe-core grep -E "def execute_(task|plan)" /app/routers/task_executor.py | head -5

echo -e "\nChecking execute_task_async:"
docker exec zoe-core grep -A 5 "executor = TaskExecutor" /app/routers/developer_tasks.py

# Step 5: Restart the service
echo -e "\n🐳 Step 5: Restarting zoe-core..."
docker compose restart zoe-core
sleep 8

# Step 6: Test with a simple task
echo -e "\n🧪 Step 6: Testing with a simple task..."
echo "-----------------------------------------"

RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/tasks/create \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Method Test",
    "objective": "Test the fixed methods",
    "requirements": ["Create file /tmp/method_test.txt with FIXED content"],
    "constraints": [],
    "acceptance_criteria": ["File exists with content"],
    "priority": "low"
  }')

TEST_ID=$(echo "$RESPONSE" | jq -r '.task_id')
echo "Created test task: $TEST_ID"

# Execute it
echo "Executing task..."
curl -s -X POST http://localhost:8000/api/developer/tasks/$TEST_ID/execute | jq '.'

# Wait for execution
sleep 5

# Check if file was created
echo -e "\n📂 Checking for test file..."
if docker exec zoe-core test -f /tmp/method_test.txt; then
    echo -e "✅ SUCCESS! File created!"
    echo "File content:"
    docker exec zoe-core cat /tmp/method_test.txt
else
    echo -e "⚠️ File not created yet"
    
    # Check logs for errors
    echo -e "\nChecking logs for errors:"
    docker logs zoe-core --tail 20 | grep -E "error|Error|ERROR|execute_task" || echo "No relevant errors"
fi

# Step 7: Retry the original test task
echo -e "\n🔄 Step 7: Retrying original test task 8d9d514a..."
echo "----------------------------------------------------"

curl -s -X POST http://localhost:8000/api/developer/tasks/8d9d514a/execute | jq '.'
sleep 5

# Check for the original test file
echo -e "\nChecking for test_execution.txt:"
if docker exec zoe-core test -f /tmp/test_execution.txt; then
    echo -e "✅ ORIGINAL TASK FINALLY WORKS!"
    docker exec zoe-core cat /tmp/test_execution.txt
else
    echo "Original task still needs investigation"
fi

# Step 8: Show all files created
echo -e "\n📁 Step 8: All test files in /tmp..."
echo "-------------------------------------"
docker exec zoe-core ls -la /tmp/*.txt 2>/dev/null || echo "No .txt files yet"

# Step 9: Check task statuses
echo -e "\n📊 Step 9: Task Status Summary..."
echo "----------------------------------"
curl -s http://localhost:8000/api/developer/tasks/list | jq '.tasks[] | {id: .id[0:8], title: .title[0:30], status, execution_count}'

echo -e "\n✅ Method mismatch fix complete!"
echo ""
echo "If files are being created now, the system is working!"
echo "If not, check: docker logs zoe-core --tail 50"
