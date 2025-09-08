#!/bin/bash
# IMMEDIATE_FIX_TASK_EXECUTOR.sh
# Fix the path issues and verify TaskExecutor works

set -e

echo "🔧 Immediate TaskExecutor Fix"
echo "=============================="

cd /home/pi/zoe

# Step 1: Quick backup
echo "📦 Creating backup..."
cp services/zoe-core/routers/task_executor.py services/zoe-core/routers/task_executor.py.backup_$(date +%Y%m%d_%H%M%S) 2>/dev/null || true
cp services/zoe-core/routers/plan_generator.py services/zoe-core/routers/plan_generator.py.backup_$(date +%Y%m%d_%H%M%S) 2>/dev/null || true

# Step 2: Fix TaskExecutor paths directly in container
echo "🔧 Fixing paths in container..."

# Fix task_executor.py
docker exec zoe-core bash -c "sed -i 's|/home/pi/zoe/|/app/|g' /app/routers/task_executor.py"

# Fix plan_generator.py if it exists
docker exec zoe-core bash -c "[ -f /app/routers/plan_generator.py ] && sed -i 's|/home/pi/zoe/|/app/|g' /app/routers/plan_generator.py || true"

# Fix developer_tasks.py if it has path issues
docker exec zoe-core bash -c "sed -i 's|/home/pi/zoe/|/app/|g' /app/routers/developer_tasks.py"

# Step 3: Also fix the source files for persistence
echo "🔧 Fixing source files..."
sed -i 's|/home/pi/zoe/|/app/|g' services/zoe-core/routers/task_executor.py 2>/dev/null || true
sed -i 's|/home/pi/zoe/|/app/|g' services/zoe-core/routers/plan_generator.py 2>/dev/null || true
sed -i 's|/home/pi/zoe/|/app/|g' services/zoe-core/routers/developer_tasks.py 2>/dev/null || true

# Step 4: Restart the container
echo "🐳 Restarting zoe-core..."
docker compose restart zoe-core

echo "⏳ Waiting for service to be ready..."
sleep 8

# Step 5: Verify service is healthy
echo "🏥 Checking service health..."
curl -s http://localhost:8000/health | jq '.' || echo "⚠️ Service not responding"

# Step 6: Check test task status
echo -e "\n📊 Checking test task 8d9d514a status..."
curl -s http://localhost:8000/api/developer/tasks/list | jq '.tasks[] | select(.id=="8d9d514a") | {id, status, execution_count}'

# Step 7: Check if test file was created
echo -e "\n📂 Checking for test file..."
docker exec zoe-core ls -la /tmp/test_execution.txt 2>/dev/null && echo "✅ Test file created!" || echo "⚠️ Test file not found"

# Step 8: Check execution history
echo -e "\n📜 Execution history:"
curl -s http://localhost:8000/api/developer/tasks/8d9d514a/history | jq '.'

# Step 9: Create a new simple test to verify
echo -e "\n🆕 Creating new verification task..."
RESPONSE=$(curl -s -X POST http://localhost:8000/api/developer/tasks/create \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Verify Fix Works",
    "objective": "Confirm path fixes are working",
    "requirements": ["Create file /tmp/fix_verified.txt with SUCCESS message"],
    "constraints": ["Must work in container"],
    "acceptance_criteria": ["File exists with content"],
    "priority": "low"
  }')

NEW_ID=$(echo "$RESPONSE" | jq -r '.task_id')
echo "Created task: $NEW_ID"

sleep 2
echo "Executing new task..."
curl -X POST http://localhost:8000/api/developer/tasks/$NEW_ID/execute

sleep 3
echo -e "\nVerification:"
docker exec zoe-core cat /tmp/fix_verified.txt 2>/dev/null && echo "✅ FIX CONFIRMED WORKING!" || echo "⚠️ Still having issues"

# Step 10: Show all tasks status
echo -e "\n📋 All Tasks Status:"
curl -s http://localhost:8000/api/developer/tasks/list | jq '.tasks[] | {id: .id[0:8], title: .title, status, priority}'

echo -e "\n✅ Fix applied! Next steps:"
echo "  1. Check if test files exist in /tmp/"
echo "  2. Try executing real tasks:"
echo "     - Redis caching: curl -X POST http://localhost:8000/api/developer/tasks/ff84a566/execute"
echo "     - Task UI: curl -X POST http://localhost:8000/api/developer/tasks/df03a0fb/execute"
echo "  3. Monitor with: docker logs -f zoe-core"
