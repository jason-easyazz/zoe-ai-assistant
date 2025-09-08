#!/bin/bash
# CHECK_EXISTING_TASKS.sh
# Purpose: Check for existing task-related implementations before adding new system

set -e

echo "🔍 Checking for Existing Task System Components"
echo "==============================================="
echo ""

cd /home/pi/zoe

# Check 1: Look for task-related files in the backend
echo "📂 Checking for task-related backend files..."
echo "-------------------------------------------"

# Check for any task_workflow files
if [ -f "services/zoe-core/routers/task_workflow.py" ]; then
    echo "✓ Found: task_workflow.py"
    echo "  First 20 lines:"
    head -20 services/zoe-core/routers/task_workflow.py
else
    echo "✗ No task_workflow.py found"
fi

# Check for task-related content in developer.py
if [ -f "services/zoe-core/routers/developer.py" ]; then
    echo -e "\n📄 Checking developer.py for task endpoints..."
    if grep -q "task" services/zoe-core/routers/developer.py; then
        echo "✓ Found task-related code in developer.py:"
        grep -n "task" services/zoe-core/routers/developer.py | head -10
    else
        echo "✗ No task-related code in developer.py"
    fi
fi

# Check for lists.py (which handles user tasks)
if [ -f "services/zoe-core/routers/lists.py" ]; then
    echo -e "\n📄 Checking lists.py for task handling..."
    if grep -q "task" services/zoe-core/routers/lists.py; then
        echo "✓ Found task-related code in lists.py:"
        grep -n "task" services/zoe-core/routers/lists.py | head -10
    else
        echo "✗ No specific task code in lists.py"
    fi
fi

# Check 2: Database tables
echo -e "\n📊 Checking database for task-related tables..."
echo "-----------------------------------------------"

# Check main database
if docker exec zoe-core sqlite3 /app/data/zoe.db ".tables" 2>/dev/null | grep -q "task"; then
    echo "✓ Found task-related tables in zoe.db:"
    docker exec zoe-core sqlite3 /app/data/zoe.db ".tables" | tr ' ' '\n' | grep -i task
    
    # Show schema if tasks table exists
    if docker exec zoe-core sqlite3 /app/data/zoe.db ".tables" 2>/dev/null | grep -q "^tasks$"; then
        echo -e "\nSchema for 'tasks' table:"
        docker exec zoe-core sqlite3 /app/data/zoe.db ".schema tasks"
    fi
else
    echo "✗ No task-specific tables found in zoe.db"
fi

# Check for separate tasks.db
if docker exec zoe-core test -f /app/data/tasks.db 2>/dev/null; then
    echo -e "\n✓ Found separate tasks.db file!"
    echo "Tables in tasks.db:"
    docker exec zoe-core sqlite3 /app/data/tasks.db ".tables" 2>/dev/null || echo "  (Unable to read)"
    
    # Show all schemas
    docker exec zoe-core sqlite3 /app/data/tasks.db ".schema" 2>/dev/null || echo "  (Unable to read schemas)"
else
    echo "✗ No separate tasks.db found"
fi

# Check 3: API endpoints
echo -e "\n🌐 Checking API endpoints for task-related routes..."
echo "----------------------------------------------------"

# Get all endpoints
ENDPOINTS=$(curl -s http://localhost:8000/openapi.json 2>/dev/null | jq -r '.paths | keys[]' 2>/dev/null || echo "")

if [ -n "$ENDPOINTS" ]; then
    echo "Task-related endpoints found:"
    echo "$ENDPOINTS" | grep -i task || echo "  ✗ No task-specific endpoints"
    echo "$ENDPOINTS" | grep -i developer | grep -v chat || echo "  ✗ No developer task endpoints"
else
    echo "⚠️ Could not retrieve API endpoints (service may be down)"
fi

# Check 4: Frontend task UI
echo -e "\n🎨 Checking frontend for task UI components..."
echo "----------------------------------------------"

# Check developer UI for task management
if [ -f "services/zoe-ui/dist/developer/index.html" ]; then
    if grep -q "task" services/zoe-ui/dist/developer/index.html; then
        echo "✓ Found task references in developer UI"
        grep -o "task[^\"]*" services/zoe-ui/dist/developer/index.html | head -5
    else
        echo "✗ No task management in developer UI"
    fi
fi

# Check for task-manager.js
if [ -f "services/zoe-ui/dist/developer/js/task-manager.js" ]; then
    echo "✓ Found task-manager.js"
    echo "  File size: $(wc -l services/zoe-ui/dist/developer/js/task-manager.js | cut -d' ' -f1) lines"
else
    echo "✗ No task-manager.js found"
fi

# Check for tasks.html page
if [ -f "services/zoe-ui/dist/tasks.html" ]; then
    echo "✓ Found tasks.html page"
    echo "  Title: $(grep -o '<title>.*</title>' services/zoe-ui/dist/tasks.html)"
else
    echo "✗ No tasks.html page found"
fi

# Check 5: Scripts related to tasks
echo -e "\n📜 Checking for task-related scripts..."
echo "----------------------------------------"

if find scripts/ -name "*task*" -type f 2>/dev/null | grep -q .; then
    echo "✓ Found task-related scripts:"
    find scripts/ -name "*task*" -type f 2>/dev/null
else
    echo "✗ No task-related scripts found"
fi

# Check 6: Docker service status
echo -e "\n🐳 Checking Docker service status..."
echo "------------------------------------"

docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep zoe- || echo "No Zoe containers running"

# Check 7: Main.py imports
echo -e "\n📦 Checking main.py for task_workflow import..."
echo "-----------------------------------------------"

if docker exec zoe-core grep -q "task_workflow" /app/main.py 2>/dev/null; then
    echo "✓ task_workflow is imported in main.py"
    docker exec zoe-core grep "task_workflow" /app/main.py
else
    echo "✗ task_workflow not imported in main.py"
fi

# Summary
echo -e "\n📊 SUMMARY"
echo "=========="

EXISTING_COMPONENTS=0

if [ -f "services/zoe-core/routers/task_workflow.py" ]; then
    echo "✓ Task workflow router exists"
    EXISTING_COMPONENTS=$((EXISTING_COMPONENTS + 1))
fi

if docker exec zoe-core test -f /app/data/tasks.db 2>/dev/null; then
    echo "✓ Tasks database exists"
    EXISTING_COMPONENTS=$((EXISTING_COMPONENTS + 1))
fi

if [ -f "services/zoe-ui/dist/developer/js/task-manager.js" ]; then
    echo "✓ Task manager UI exists"
    EXISTING_COMPONENTS=$((EXISTING_COMPONENTS + 1))
fi

if [ $EXISTING_COMPONENTS -gt 0 ]; then
    echo -e "\n⚠️ WARNING: Found $EXISTING_COMPONENTS existing task components"
    echo "Options:"
    echo "  1. Back up existing components before proceeding"
    echo "  2. Review existing implementation"
    echo "  3. Decide whether to upgrade or replace"
else
    echo -e "\n✅ No existing developer task system found"
    echo "Safe to proceed with new implementation"
fi

echo -e "\n📌 Note: User lists/tasks feature is separate from developer task workflow"
echo "  - User lists (Zoe): /api/lists/* endpoints"
echo "  - Developer tasks: /api/tasks/* endpoints (if exists)"
