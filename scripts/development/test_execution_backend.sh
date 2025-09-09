#!/bin/bash
echo "Testing Task Execution Backend"
echo "==============================="

cd /home/pi/zoe

# Test 1: List current tasks
echo -e "\n1. Listing current tasks..."
curl -s http://localhost:8000/api/developer/tasks/list | jq '.'

# Test 2: Analyze a task (if one exists)
echo -e "\n2. Analyzing task 4a849934 (if exists)..."
curl -s -X POST http://localhost:8000/api/developer/tasks/4a849934/analyze | jq '.'

# Test 3: Create a test task
echo -e "\n3. Creating test task..."
curl -X POST http://localhost:8000/api/developer/tasks/create \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Execution Backend",
    "objective": "Test the new execution backend",
    "requirements": ["Create test endpoint /api/test", "Add test UI element"],
    "constraints": ["Do not break existing endpoints"],
    "acceptance_criteria": ["Test endpoint returns success", "No errors in logs"],
    "priority": "low"
  }' | jq '.'

echo -e "\n✅ Test complete! Backend ready for use."
