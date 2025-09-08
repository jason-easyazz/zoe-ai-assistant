#!/bin/bash
# DIAGNOSE_TASK_EXECUTOR.sh
# Location: scripts/maintenance/diagnose_task_executor.sh
# Purpose: Find out why tasks complete without executing

set -e

echo "🔍 Diagnosing TaskExecutor Issues"
echo "=================================="

cd /home/pi/zoe

# Step 1: Check container logs for errors
echo "📜 Step 1: Recent TaskExecutor logs..."
echo "----------------------------------------"
docker logs zoe-core --tail 100 | grep -E "TaskExecutor|executing|step|file_create|shell" | tail -20 || echo "No TaskExecutor logs found"

# Step 2: Check if TaskExecutor is imported correctly
echo -e "\n🔧 Step 2: Checking TaskExecutor import..."
echo "----------------------------------------"
docker exec zoe-core python3 -c "
import sys
sys.path.append('/app')
try:
    from routers.task_executor import TaskExecutor
    print('✅ TaskExecutor imported successfully')
    executor = TaskExecutor('test123')
    print(f'✅ TaskExecutor initialized: {executor.task_id}')
except Exception as e:
    print(f'❌ Error: {e}')
" 2>&1

# Step 3: Check if plan_generator works
echo -e "\n📋 Step 3: Testing plan generation..."
echo "----------------------------------------"
docker exec zoe-core python3 -c "
import sys
sys.path.append('/app')
try:
    from routers.plan_generator import PlanGenerator
    generator = PlanGenerator()
    plan = generator.generate_plan(
        objective='Create test file',
        requirements=['Create /tmp/test.txt'],
        constraints=[],
        context={}
    )
    print('✅ Plan generated:')
    import json
    print(json.dumps(plan, indent=2))
except Exception as e:
    print(f'❌ Error: {e}')
" 2>&1

# Step 4: Test direct file creation in container
echo -e "\n📁 Step 4: Testing direct file operations..."
echo "----------------------------------------"
docker exec zoe-core python3 -c "
import os
try:
    # Test write permission
    with open('/tmp/direct_test.txt', 'w') as f:
        f.write('Direct test successful')
    print('✅ Can write to /tmp/')
    
    # List /tmp contents
    files = os.listdir('/tmp')
    print(f'Files in /tmp: {files}')
except Exception as e:
    print(f'❌ Error: {e}')
" 2>&1

# Step 5: Check database for execution details
echo -e "\n💾 Step 5: Database execution records..."
echo "----------------------------------------"
docker exec zoe-core sqlite3 /app/data/developer_tasks.db "
SELECT task_id, execution_time, success, 
       substr(execution_result, 1, 100) as result_preview
FROM task_executions 
ORDER BY execution_time DESC 
LIMIT 5;" 2>/dev/null || echo "Could not query database"

# Step 6: Create and execute a minimal test task
echo -e "\n🧪 Step 6: Creating minimal test task..."
echo "----------------------------------------"

# Create a super simple task
RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/tasks/create \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Debug Test",
    "objective": "Test minimal execution",
    "requirements": ["Print hello to console"],
    "constraints": [],
    "acceptance_criteria": ["No errors"],
    "priority": "low"
  }')

TEST_ID=$(echo "$RESPONSE" | jq -r '.task_id')
echo "Created debug task: $TEST_ID"

# Get the analysis
echo -e "\nAnalyzing task..."
curl -s -X POST http://localhost:8000/api/developer/tasks/$TEST_ID/analyze | jq '.plan'

# Execute it
echo -e "\nExecuting task..."
EXEC_RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/tasks/$TEST_ID/execute)
echo "$EXEC_RESPONSE" | jq '.'

# Wait and check
sleep 3
echo -e "\nChecking execution result..."
curl -s http://localhost:8000/api/developer/tasks/$TEST_ID/history | jq '.executions[0]'

# Step 7: Check TaskExecutor file content
echo -e "\n📄 Step 7: TaskExecutor file check..."
echo "----------------------------------------"
docker exec zoe-core head -20 /app/routers/task_executor.py | grep -E "class|def execute|def.*step" || echo "Could not read TaskExecutor"

# Step 8: Test TaskExecutor directly
echo -e "\n🔬 Step 8: Direct TaskExecutor test..."
echo "----------------------------------------"
docker exec zoe-core python3 -c "
import sys
import json
sys.path.append('/app')

try:
    from routers.task_executor import TaskExecutor
    
    # Create executor
    executor = TaskExecutor('direct_test')
    
    # Create a simple plan
    plan = {
        'steps': [
            {
                'type': 'shell',
                'command': 'echo \"Testing\" > /tmp/executor_test.txt',
                'description': 'Create test file'
            }
        ]
    }
    
    # Execute
    print('Executing plan...')
    result = executor.execute_plan(plan)
    print(f'Result: {json.dumps(result, indent=2)}')
    
    # Check if file was created
    import os
    if os.path.exists('/tmp/executor_test.txt'):
        print('✅ TaskExecutor created file successfully!')
    else:
        print('❌ File not created by TaskExecutor')
        
except Exception as e:
    import traceback
    print(f'❌ Error: {e}')
    traceback.print_exc()
" 2>&1

# Step 9: Check for any /tmp files created
echo -e "\n📂 Step 9: All files in /tmp..."
echo "----------------------------------------"
docker exec zoe-core ls -la /tmp/ | head -20

# Summary
echo -e "\n📊 Diagnosis Summary"
echo "===================="
echo "1. Check if TaskExecutor imports correctly"
echo "2. Check if plans are being generated"
echo "3. Check if container can write to /tmp"
echo "4. Check if TaskExecutor.execute_plan() works"
echo "5. Check database for execution records"
echo ""
echo "Look for ❌ marks above to identify the issue."
echo ""
echo "Possible issues:"
echo "  - TaskExecutor not properly integrated"
echo "  - Plan generator not creating executable steps"
echo "  - Execute function not calling TaskExecutor"
echo "  - Permission issues in container"
