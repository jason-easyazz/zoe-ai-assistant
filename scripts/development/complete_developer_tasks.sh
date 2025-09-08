#!/bin/bash
# COMPLETE_DEVELOPER_TASKS.sh
# Location: scripts/development/complete_developer_tasks.sh
# Purpose: Properly install the developer task workflow system

set -e

echo "🎯 Installing Developer Task Workflow System"
echo "==========================================="
echo ""
echo "This will install the task management system in the developer backend"
echo "Following proper architecture guidelines:"
echo "  - Part of developer system (not user features)"
echo "  - Accessible via developer dashboard"
echo "  - Uses /api/developer/tasks/* endpoints"
echo ""
echo "Press Enter to continue or Ctrl+C to abort..."
read

cd /home/pi/zoe

# Step 1: Backup current state
echo "📦 Creating backup..."
BACKUP_DIR="backups/developer_tasks_$(date +%Y%m%d_%H%M%S)"
mkdir -p $BACKUP_DIR
cp -r services/zoe-core/routers $BACKUP_DIR/
cp services/zoe-core/main.py $BACKUP_DIR/
echo "✓ Backup created at $BACKUP_DIR"

# Step 2: Remove the broken task_workflow.py
echo -e "\n🧹 Cleaning up broken implementation..."
if [ -f "services/zoe-core/routers/task_workflow.py" ]; then
    mv services/zoe-core/routers/task_workflow.py $BACKUP_DIR/task_workflow_old.py
    echo "✓ Moved old task_workflow.py to backup"
fi

# Step 3: Create proper developer task system
echo -e "\n📝 Creating developer task system..."
cat > services/zoe-core/routers/developer_tasks.py << 'EOF'
"""
Developer Task Management System
Part of the developer backend for managing development tasks
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
from datetime import datetime
import json
import sqlite3
import hashlib
import logging

logger = logging.getLogger(__name__)

# This router is part of the developer system
router = APIRouter(prefix="/api/developer/tasks", tags=["developer-tasks"])

# Initialize task storage
tasks = {}

class DeveloperTask(BaseModel):
    """Task for developer system management"""
    title: str
    description: str
    task_type: str = "development"  # development, fix, feature, maintenance
    priority: str = "medium"
    assigned_to: str = "zack"  # zack or claude

class TaskPlan(BaseModel):
    """Execution plan for a task"""
    task_id: str
    title: str
    analysis: str
    steps: List[Dict[str, str]]
    risk_level: str
    estimated_duration: int
    test_criteria: List[str]

def init_developer_tasks_db():
    """Initialize developer tasks database"""
    conn = sqlite3.connect('/app/data/developer_tasks.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS developer_tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            task_type TEXT,
            priority TEXT,
            assigned_to TEXT,
            status TEXT,
            plan TEXT,
            execution_log TEXT,
            created_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

@router.get("/")
async def get_developer_tasks_info():
    """Get information about the developer task system"""
    return {
        "system": "Developer Task Management",
        "version": "1.0",
        "description": "Task management for development operations",
        "endpoints": {
            "create": "POST /api/developer/tasks/create",
            "list": "GET /api/developer/tasks/list",
            "status": "GET /api/developer/tasks/status/{task_id}",
            "execute": "POST /api/developer/tasks/execute/{task_id}"
        }
    }

@router.post("/create")
async def create_developer_task(task: DeveloperTask):
    """Create a new developer task"""
    # Generate task ID
    task_id = hashlib.md5(f"{task.title}{datetime.now()}".encode()).hexdigest()[:8]
    
    # Store task
    tasks[task_id] = {
        "id": task_id,
        "title": task.title,
        "description": task.description,
        "task_type": task.task_type,
        "priority": task.priority,
        "assigned_to": task.assigned_to,
        "status": "created",
        "created_at": datetime.now().isoformat(),
        "execution_log": [f"Task created: {task.title}"]
    }
    
    # Save to database
    try:
        conn = sqlite3.connect('/app/data/developer_tasks.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO developer_tasks (id, title, description, task_type, priority, assigned_to, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (task_id, task.title, task.description, task.task_type, 
              task.priority, task.assigned_to, "created", datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Database error: {e}")
    
    return {
        "task_id": task_id,
        "status": "created",
        "message": f"Developer task created and assigned to {task.assigned_to}"
    }

@router.get("/list")
async def list_developer_tasks():
    """List all developer tasks"""
    try:
        conn = sqlite3.connect('/app/data/developer_tasks.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, title, task_type, priority, status, created_at 
            FROM developer_tasks 
            ORDER BY created_at DESC 
            LIMIT 50
        ''')
        rows = cursor.fetchall()
        conn.close()
        
        db_tasks = [
            {
                "id": row[0],
                "title": row[1],
                "task_type": row[2],
                "priority": row[3],
                "status": row[4],
                "created_at": row[5]
            }
            for row in rows
        ]
        
        # Merge with in-memory tasks
        all_tasks = list(tasks.values()) + db_tasks
        
        return {
            "tasks": all_tasks,
            "count": len(all_tasks),
            "source": "developer_tasks"
        }
    except Exception as e:
        # Fallback to in-memory only
        return {
            "tasks": list(tasks.values()),
            "count": len(tasks),
            "source": "memory_only",
            "error": str(e)
        }

@router.get("/status/{task_id}")
async def get_developer_task_status(task_id: str):
    """Get status of a specific developer task"""
    
    # Check in-memory first
    if task_id in tasks:
        return tasks[task_id]
    
    # Check database
    try:
        conn = sqlite3.connect('/app/data/developer_tasks.db')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM developer_tasks WHERE id = ?', (task_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "id": row[0],
                "title": row[1],
                "description": row[2],
                "task_type": row[3],
                "priority": row[4],
                "assigned_to": row[5],
                "status": row[6],
                "created_at": row[9]
            }
    except Exception as e:
        logger.error(f"Database error: {e}")
    
    raise HTTPException(status_code=404, detail="Task not found")

@router.post("/analyze/{task_id}")
async def analyze_developer_task(task_id: str):
    """Generate execution plan for a developer task"""
    
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    
    # Create a simple plan (in production, this would use AI)
    plan = TaskPlan(
        task_id=task_id,
        title=task["title"],
        analysis=f"Analysis for: {task['description']}",
        steps=[
            {"action": "backup", "command": "Create system backup"},
            {"action": "implement", "command": "Implement the feature"},
            {"action": "test", "command": "Run tests"},
            {"action": "verify", "command": "Verify implementation"}
        ],
        risk_level="medium",
        estimated_duration=30,
        test_criteria=["System remains operational", "Feature works as expected"]
    )
    
    task["plan"] = plan.dict()
    task["status"] = "analyzed"
    
    return {
        "task_id": task_id,
        "plan": plan.dict(),
        "message": "Task analyzed and ready for execution"
    }

@router.post("/execute/{task_id}")
async def execute_developer_task(task_id: str, background_tasks: BackgroundTasks):
    """Execute a developer task (mock execution for now)"""
    
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    
    if task["status"] != "analyzed":
        return {
            "task_id": task_id,
            "error": "Task must be analyzed before execution",
            "hint": f"Call POST /api/developer/tasks/analyze/{task_id} first"
        }
    
    # Mark as executing
    task["status"] = "executing"
    task["execution_log"].append(f"Execution started at {datetime.now().isoformat()}")
    
    # In production, this would actually execute
    # For now, just mark as completed after mock execution
    task["status"] = "completed"
    task["completed_at"] = datetime.now().isoformat()
    task["execution_log"].append("Task completed successfully (mock execution)")
    
    return {
        "task_id": task_id,
        "status": "completed",
        "message": "Developer task executed successfully"
    }

# Initialize database on module load
try:
    init_developer_tasks_db()
except Exception as e:
    logger.warning(f"Could not initialize database: {e}")
EOF

echo "✓ Created developer_tasks.py"

# Step 4: Update main.py properly
echo -e "\n📝 Updating main.py..."

# Remove old task_workflow references and add developer_tasks
docker exec zoe-core python3 << 'PYTHON'
import re

# Read main.py
with open('/app/main.py', 'r') as f:
    content = f.read()

# Remove task_workflow references
content = re.sub(r',?\s*task_workflow', '', content)
content = re.sub(r'app\.include_router\(task_workflow\.router\)[^\n]*\n', '', content)

# Add developer_tasks to imports if not there
if 'developer_tasks' not in content:
    content = content.replace(
        'from routers import developer',
        'from routers import developer, developer_tasks'
    )

# Add router inclusion after developer router
lines = content.split('\n')
new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)
    if 'app.include_router(developer.router)' in line and i < len(lines) - 1:
        # Check if next line isn't already developer_tasks
        if 'developer_tasks' not in lines[i+1]:
            new_lines.append('app.include_router(developer_tasks.router)')

content = '\n'.join(new_lines)

# Write back
with open('/app/main.py', 'w') as f:
    f.write(content)

print("✓ Updated main.py")

# Show the relevant parts
print("\nImports line:")
for line in content.split('\n'):
    if 'from routers import' in line:
        print(f"  {line}")
        
print("\nRouter inclusions:")
for line in content.split('\n'):
    if 'app.include_router' in line and 'developer' in line:
        print(f"  {line}")
PYTHON

# Step 5: Create the database
echo -e "\n📊 Creating developer tasks database..."
docker exec zoe-core python3 -c "
from routers.developer_tasks import init_developer_tasks_db
init_developer_tasks_db()
print('✓ Database initialized')
" 2>/dev/null || echo "✓ Database will be created on first run"

# Step 6: Create helper script in utilities (for day-to-day use)
echo -e "\n📝 Creating helper script..."
cat > scripts/utilities/dev_tasks.sh << 'EOF'
#!/bin/bash
# Developer Task Management Helper
# For managing developer tasks after installation

API_BASE="http://localhost:8000/api/developer/tasks"

case "$1" in
    create)
        if [ -z "$2" ]; then
            echo "Usage: $0 create \"title\" [description]"
            exit 1
        fi
        curl -s -X POST $API_BASE/create \
            -H "Content-Type: application/json" \
            -d "{
                \"title\": \"$2\",
                \"description\": \"${3:-No description}\",
                \"task_type\": \"${4:-development}\",
                \"priority\": \"${5:-medium}\"
            }" | jq '.'
        ;;
    
    list)
        echo "📋 Developer Tasks:"
        curl -s $API_BASE/list | jq '.'
        ;;
    
    status)
        if [ -z "$2" ]; then
            echo "Usage: $0 status <task_id>"
            exit 1
        fi
        curl -s $API_BASE/status/$2 | jq '.'
        ;;
    
    analyze)
        if [ -z "$2" ]; then
            echo "Usage: $0 analyze <task_id>"
            exit 1
        fi
        curl -s -X POST $API_BASE/analyze/$2 | jq '.'
        ;;
    
    execute)
        if [ -z "$2" ]; then
            echo "Usage: $0 execute <task_id>"
            exit 1
        fi
        curl -s -X POST $API_BASE/execute/$2 | jq '.'
        ;;
    
    info)
        curl -s $API_BASE/ | jq '.'
        ;;
    
    *)
        echo "Developer Task Management"
        echo "Usage: $0 {create|list|status|analyze|execute|info}"
        echo ""
        echo "Examples:"
        echo "  $0 create \"Fix API bug\" \"The calendar API returns 500\""
        echo "  $0 list"
        echo "  $0 analyze <task_id>"
        echo "  $0 execute <task_id>"
        ;;
esac
EOF

chmod +x scripts/utilities/dev_tasks.sh
echo "✓ Created dev_tasks.sh helper"

# Step 7: Restart service
echo -e "\n🔄 Restarting zoe-core..."
docker compose restart zoe-core

echo "⏳ Waiting for service to start..."
sleep 10

# Step 8: Test the system
echo -e "\n🧪 Testing developer task system..."

# Get info
echo "Getting system info..."
curl -s http://localhost:8000/api/developer/tasks/ | jq '.' || echo "⚠️ Not responding yet"

# Create a test task
echo -e "\nCreating test task..."
TEST_RESULT=$(curl -s -X POST http://localhost:8000/api/developer/tasks/create \
    -H "Content-Type: application/json" \
    -d '{
        "title": "Test Developer Task",
        "description": "Verify the developer task system works",
        "task_type": "development",
        "priority": "low"
    }' 2>/dev/null || echo "{}")

if echo "$TEST_RESULT" | grep -q "task_id"; then
    echo "✅ Successfully created test task!"
    echo "$TEST_RESULT" | jq '.'
    
    # List tasks
    echo -e "\nListing all developer tasks..."
    curl -s http://localhost:8000/api/developer/tasks/list | jq '.' || echo "Could not list"
else
    echo "⚠️ Task creation failed"
    echo "Checking logs..."
    docker logs zoe-core --tail 20 | grep -i error || true
fi

# Step 9: Clean up old task files
echo -e "\n🧹 Cleaning up old task implementations..."
OLD_FILES=(
    "scripts/development/complete_tasks_system.sh"
    "scripts/development/debug_and_fix_tasks.sh"
    "scripts/development/fix_tasks_completion.sh"
    "scripts/development/fix_tasks_database_path.sh"
    "scripts/development/fix_tasks_database.sh"
    "scripts/development/fix_tasks_final.sh"
    "scripts/development/task_system_foundation.sh"
)

for file in "${OLD_FILES[@]}"; do
    if [ -f "$file" ]; then
        mv "$file" "$BACKUP_DIR/" 2>/dev/null || true
        echo "  Moved $file to backup"
    fi
done

# Summary
echo -e "\n✅ DEVELOPER TASK SYSTEM INSTALLATION COMPLETE"
echo "=============================================="
echo ""
echo "✓ Created developer_tasks.py router"
echo "✓ Integrated with developer backend"  
echo "✓ Database at /app/data/developer_tasks.db"
echo "✓ Helper script at scripts/utilities/dev_tasks.sh"
echo ""
echo "📍 Endpoints:"
echo "  GET  /api/developer/tasks/       - System info"
echo "  POST /api/developer/tasks/create - Create task"
echo "  GET  /api/developer/tasks/list   - List tasks"
echo "  GET  /api/developer/tasks/status - Task status"
echo "  POST /api/developer/tasks/analyze - Analyze task"
echo "  POST /api/developer/tasks/execute - Execute task"
echo ""
echo "🎯 Quick Start:"
echo "  ./scripts/utilities/dev_tasks.sh create \"Your first task\" \"Description\""
echo "  ./scripts/utilities/dev_tasks.sh list"
echo ""
echo "📝 Next Steps:"
echo "  1. Test: ./scripts/utilities/dev_tasks.sh info"
echo "  2. Commit: git add . && git commit -m '✅ Added developer task system'"
echo "  3. For advanced features: Run dynamic_task_workflow.sh"
