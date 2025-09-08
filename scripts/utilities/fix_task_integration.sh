#!/bin/bash
# FIX_TASK_INTEGRATION.sh
# Purpose: Diagnose and fix why task endpoints aren't showing

set -e

echo "🔍 Diagnosing Task System Integration"
echo "====================================="
echo ""

cd /home/pi/zoe

# Step 1: Check if task_workflow is actually imported
echo "📦 Checking main.py imports..."
if docker exec zoe-core cat /app/main.py | grep -q "task_workflow"; then
    echo "✓ task_workflow is in main.py"
    docker exec zoe-core grep "task_workflow" /app/main.py
else
    echo "✗ task_workflow NOT in main.py!"
fi

# Step 2: Check if the router is included
echo -e "\n🔌 Checking router inclusion..."
if docker exec zoe-core cat /app/main.py | grep -q "app.include_router(task_workflow.router)"; then
    echo "✓ Router is included"
else
    echo "✗ Router NOT included - fixing..."
    
    # Fix it
    docker exec zoe-core python3 << 'PYTHON'
with open('/app/main.py', 'r') as f:
    lines = f.readlines()

# Check if we need to add the include
needs_include = True
for line in lines:
    if 'task_workflow.router' in line:
        needs_include = False
        break

if needs_include:
    # Find where to add it (after developer.router)
    for i, line in enumerate(lines):
        if 'app.include_router(developer.router)' in line:
            lines.insert(i+1, 'app.include_router(task_workflow.router)\n')
            break
    
    with open('/app/main.py', 'w') as f:
        f.writelines(lines)
    print("✓ Added router inclusion")
else:
    print("✓ Router already included")
PYTHON
fi

# Step 3: Check if task_workflow.py exists and has content
echo -e "\n📄 Checking task_workflow.py..."
if docker exec zoe-core test -f /app/routers/task_workflow.py; then
    LINE_COUNT=$(docker exec zoe-core wc -l /app/routers/task_workflow.py | cut -d' ' -f1)
    echo "✓ task_workflow.py exists with $LINE_COUNT lines"
    
    if [ "$LINE_COUNT" -lt "100" ]; then
        echo "⚠️ File seems incomplete (only $LINE_COUNT lines)"
        echo "Need to restore the full version"
        NEEDS_FULL_VERSION=true
    else
        echo "✓ File appears complete"
        NEEDS_FULL_VERSION=false
    fi
else
    echo "✗ task_workflow.py missing!"
    NEEDS_FULL_VERSION=true
fi

# Step 4: Check for import errors
echo -e "\n🐍 Checking for Python import errors..."
docker exec zoe-core python3 << 'PYTHON' 2>&1 | grep -v "Warning" || true
try:
    import sys
    sys.path.append('/app')
    from routers import task_workflow
    print("✓ task_workflow imports successfully")
    
    # Check if router exists
    if hasattr(task_workflow, 'router'):
        print("✓ Router object exists")
        
        # Try to see routes
        if hasattr(task_workflow.router, 'routes'):
            print(f"✓ Found {len(task_workflow.router.routes)} routes")
    else:
        print("✗ No router object found!")
except Exception as e:
    print(f"✗ Import error: {e}")
PYTHON

# Step 5: Quick fix - ensure minimal working version
echo -e "\n🔧 Ensuring minimal working task_workflow.py..."

docker exec zoe-core python3 << 'PYTHON'
import os

# Check if we need to create a minimal version
minimal_code = '''"""
Task Workflow System - Minimal Working Version
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
import json

router = APIRouter(prefix="/api/tasks", tags=["task-workflow"])

# Simple in-memory storage
tasks = {}

class TaskRequest(BaseModel):
    title: str
    description: str
    priority: str = "medium"

@router.get("/health")
async def health_check():
    return {"status": "active", "message": "Task workflow system is running"}

@router.post("/create")
async def create_task(request: TaskRequest):
    """Create a new task"""
    task_id = str(len(tasks) + 1)
    tasks[task_id] = {
        "id": task_id,
        "title": request.title,
        "description": request.description,
        "priority": request.priority,
        "status": "created",
        "created_at": datetime.now().isoformat()
    }
    return {"task_id": task_id, "status": "created", "task": tasks[task_id]}

@router.get("/list")
async def list_tasks():
    """List all tasks"""
    return {"tasks": list(tasks.values()), "count": len(tasks)}

@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """Get task status"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]

print("✓ Minimal task workflow ready")
'''

# Write the minimal version if current one is broken
path = '/app/routers/task_workflow.py'
if not os.path.exists(path) or os.path.getsize(path) < 1000:
    with open(path, 'w') as f:
        f.write(minimal_code)
    print("✓ Created minimal working version")
else:
    print("✓ Keeping existing version")
PYTHON

# Step 6: Restart the service
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core

# Wait for restart
echo "⏳ Waiting for service..."
sleep 8

# Step 7: Final test
echo -e "\n✅ Final Testing..."

# Test the health endpoint
echo "Testing task health endpoint..."
HEALTH=$(curl -s http://localhost:8000/api/tasks/health 2>/dev/null || echo "{}")

if echo "$HEALTH" | grep -q "status"; then
    echo "✅ Task system is NOW ACTIVE!"
    echo "$HEALTH" | jq '.'
else
    echo "⚠️ Still not responding, checking logs..."
    docker logs zoe-core --tail 20 | grep -i "task\|error" || true
fi

# Check all endpoints
echo -e "\nTask endpoints available:"
curl -s http://localhost:8000/openapi.json 2>/dev/null | jq '.paths | keys[]' 2>/dev/null | grep "/api/tasks" || echo "No task endpoints found"

# Test creating a task
echo -e "\n🧪 Testing task creation..."
TEST_TASK=$(curl -s -X POST http://localhost:8000/api/tasks/create \
    -H "Content-Type: application/json" \
    -d '{"title": "Test Task", "description": "Testing the system", "priority": "low"}' 2>/dev/null || echo "{}")

if echo "$TEST_TASK" | grep -q "task_id"; then
    echo "✅ Successfully created test task!"
    echo "$TEST_TASK" | jq '.'
else
    echo "⚠️ Could not create task"
fi

echo -e "\n📊 SUMMARY"
echo "=========="

if curl -s http://localhost:8000/api/tasks/health 2>/dev/null | grep -q "status"; then
    echo "✅ Task system is WORKING!"
    echo ""
    echo "Available commands:"
    echo "  ./scripts/utilities/manage_tasks.sh health"
    echo "  ./scripts/utilities/manage_tasks.sh create \"Title\" \"Description\""
    echo "  ./scripts/utilities/manage_tasks.sh list"
    echo ""
    echo "To upgrade to full dynamic version:"
    echo "  ./scripts/development/dynamic_task_workflow.sh"
else
    echo "⚠️ Task system still needs attention"
    echo ""
    echo "Check logs with:"
    echo "  docker logs zoe-core --tail 50 | grep -i error"
    echo ""
    echo "Or try full reinstall:"
    echo "  ./scripts/development/dynamic_task_workflow.sh"
fi
