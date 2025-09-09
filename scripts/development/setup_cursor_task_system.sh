#!/bin/bash
# SETUP_CURSOR_TASK_SYSTEM.sh
# Location: /home/pi/zoe/scripts/development/setup_cursor_task_system.sh
# Purpose: Initialize Cursor context and populate task queue

set -e

echo "🚀 SETTING UP CURSOR + ZACK UNIFIED DEVELOPMENT SYSTEM"
echo "======================================================"
echo ""
echo "This will:"
echo "  1. Create Cursor documentation files"
echo "  2. Populate task database with development tasks"
echo "  3. Add new API endpoints for task management"
echo "  4. Test the system"
echo ""
echo "Press Enter to continue..."
read

cd /home/pi/zoe

# Step 1: Create documentation directory and files
echo -e "\n📚 Creating Cursor documentation..."
mkdir -p documentation

# Create cursor_context.md (already provided above, just reference it)
echo "Creating documentation/cursor_context.md..."
# Copy the cursor_context.md content from the artifact above

# Create .cursorrules (already provided above)
echo "Creating .cursorrules..."
# Copy the .cursorrules content from the artifact above

# Step 2: Backup existing task database
echo -e "\n💾 Backing up existing task database..."
if [ -f "data/developer_tasks.db" ]; then
    cp data/developer_tasks.db "data/developer_tasks.backup_$(date +%Y%m%d_%H%M%S).db"
fi

# Step 3: Initialize task database with new schema
echo -e "\n📊 Setting up enhanced task database..."
sqlite3 data/developer_tasks.db << 'SQL'
-- Enhanced task table for Cursor + Zack
CREATE TABLE IF NOT EXISTS dynamic_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    requirements TEXT,  -- What needs to be achieved
    objectives TEXT,    -- Specific measurable goals
    category TEXT DEFAULT 'general',
    priority TEXT DEFAULT 'medium',  -- critical, high, medium, low
    status TEXT DEFAULT 'pending',   -- pending, claimed, in_progress, completed, failed
    assignee TEXT,      -- 'Cursor', 'Zack', or specific developer
    claimed_by TEXT,    -- Who actually claimed it
    claimed_at DATETIME,
    started_at DATETIME,
    completed_at DATETIME,
    dependencies TEXT,  -- JSON array of task_ids
    completion_check TEXT,  -- How to verify completion
    result TEXT,        -- Outcome/artifacts created
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_task_status ON dynamic_tasks(status);
CREATE INDEX IF NOT EXISTS idx_task_priority ON dynamic_tasks(priority);
CREATE INDEX IF NOT EXISTS idx_task_assignee ON dynamic_tasks(assignee);

-- Insert Phase 1 tasks
INSERT OR IGNORE INTO dynamic_tasks (task_id, title, requirements, objectives, category, priority, assignee, completion_check)
VALUES 
('TASK-101', 
 'Create Cursor Context Documentation',
 'Comprehensive system context for Cursor IDE',
 'Create /home/pi/zoe/documentation/cursor_context.md with all API endpoints, file structures, Docker services',
 'documentation', 'critical', 'Cursor',
 'File exists at documentation/cursor_context.md'),

('TASK-102', 
 'Create .cursorrules File',
 'Cursor-specific development rules',
 'Create /home/pi/zoe/.cursorrules with coding standards, ARM64 considerations, forbidden actions',
 'documentation', 'critical', 'Cursor',
 '.cursorrules exists with all rules'),

('TASK-103',
 'Enhance Task System API',
 'Make task system accessible via REST API',
 'Add endpoints: GET /api/developer/tasks/next, POST /api/developer/tasks/{id}/complete, WebSocket /ws/tasks',
 'api', 'critical', 'Zack',
 'curl http://localhost:8000/api/developer/tasks/next returns task'),

('TASK-201',
 'Fix AI Response Generation',
 'Zack must generate actual code, not descriptions',
 'Update developer.py to return executable code, add code detection and formatting',
 'ai', 'high', 'Cursor',
 'Zack generates runnable code when asked'),

('TASK-202',
 'Complete RouteLLM + LiteLLM Integration',
 'Intelligent multi-model routing',
 'Keep RouteLLM for analysis, add LiteLLM as provider, configure routing rules',
 'ai', 'high', 'Zack',
 'Complex queries route to appropriate model'),

('TASK-203',
 'Add Task Execution Engine',
 'Zack can execute tasks autonomously',
 'Add background processor, safe execution, rollback on failure, execution logs',
 'automation', 'high', 'Cursor',
 'Zack successfully completes a task from queue'),

('TASK-204',
 'Create Developer Dashboard Controls',
 'Full control panel in developer UI',
 'Add task queue viz, Execute button, logs, task creation form',
 'ui', 'high', 'Cursor',
 'Dashboard shows tasks and can trigger execution');
SQL

echo "✅ Task database initialized with $(sqlite3 data/developer_tasks.db 'SELECT COUNT(*) FROM dynamic_tasks') tasks"

# Step 4: Create enhanced task API endpoints
echo -e "\n🔌 Creating enhanced task API..."
cat > services/zoe-core/routers/task_management.py << 'PYTHON'
"""Enhanced Task Management API for Cursor + Zack"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict
import sqlite3
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/developer/tasks", tags=["tasks"])

class TaskClaim(BaseModel):
    claimed_by: str  # "Cursor" or "Zack" or developer name

class TaskComplete(BaseModel):
    result: str
    success: bool = True

class TaskCreate(BaseModel):
    title: str
    requirements: str
    objectives: str
    category: str = "general"
    priority: str = "medium"
    assignee: Optional[str] = None

def get_db():
    """Get database connection"""
    conn = sqlite3.connect("/app/data/developer_tasks.db")
    conn.row_factory = sqlite3.Row
    return conn

@router.get("/")
async def list_tasks(status: Optional[str] = None, assignee: Optional[str] = None):
    """List all tasks with optional filters"""
    conn = get_db()
    cursor = conn.cursor()
    
    query = "SELECT * FROM dynamic_tasks WHERE 1=1"
    params = []
    
    if status:
        query += " AND status = ?"
        params.append(status)
    if assignee:
        query += " AND assignee = ?"
        params.append(assignee)
    
    query += " ORDER BY priority DESC, created_at ASC"
    
    cursor.execute(query, params)
    tasks = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return {"tasks": tasks, "count": len(tasks)}

@router.get("/next")
async def get_next_task(assignee: Optional[str] = None):
    """Get next unclaimed task for assignee"""
    conn = get_db()
    cursor = conn.cursor()
    
    query = """
        SELECT * FROM dynamic_tasks 
        WHERE status = 'pending'
    """
    params = []
    
    if assignee:
        query += " AND (assignee = ? OR assignee IS NULL)"
        params.append(assignee)
    
    query += " ORDER BY priority DESC, created_at ASC LIMIT 1"
    
    cursor.execute(query, params)
    task = cursor.fetchone()
    conn.close()
    
    if task:
        return dict(task)
    return {"message": "No pending tasks available"}

@router.post("/{task_id}/claim")
async def claim_task(task_id: str, claim: TaskClaim):
    """Claim a task for execution"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE dynamic_tasks 
        SET status = 'claimed',
            claimed_by = ?,
            claimed_at = ?,
            updated_at = ?
        WHERE task_id = ? AND status = 'pending'
    """, (claim.claimed_by, datetime.now(), datetime.now(), task_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Task not found or already claimed")
    
    conn.commit()
    conn.close()
    
    return {"message": f"Task {task_id} claimed by {claim.claimed_by}"}

@router.post("/{task_id}/complete")
async def complete_task(task_id: str, completion: TaskComplete):
    """Mark task as completed"""
    conn = get_db()
    cursor = conn.cursor()
    
    status = "completed" if completion.success else "failed"
    
    cursor.execute("""
        UPDATE dynamic_tasks 
        SET status = ?,
            result = ?,
            completed_at = ?,
            updated_at = ?
        WHERE task_id = ?
    """, (status, completion.result, datetime.now(), datetime.now(), task_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    
    conn.commit()
    conn.close()
    
    # Log completion
    logger.info(f"Task {task_id} marked as {status}: {completion.result}")
    
    return {"message": f"Task {task_id} marked as {status}"}

@router.post("/")
async def create_task(task: TaskCreate):
    """Create a new task"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Generate task ID
    cursor.execute("SELECT COUNT(*) as count FROM dynamic_tasks")
    count = cursor.fetchone()["count"]
    task_id = f"TASK-{1000 + count}"
    
    cursor.execute("""
        INSERT INTO dynamic_tasks 
        (task_id, title, requirements, objectives, category, priority, assignee)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (task_id, task.title, task.requirements, task.objectives, 
          task.category, task.priority, task.assignee))
    
    conn.commit()
    conn.close()
    
    return {"task_id": task_id, "message": "Task created successfully"}

@router.get("/{task_id}")
async def get_task(task_id: str):
    """Get specific task details"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM dynamic_tasks WHERE task_id = ?", (task_id,))
    task = cursor.fetchone()
    conn.close()
    
    if task:
        return dict(task)
    raise HTTPException(status_code=404, detail="Task not found")
PYTHON

# Step 5: Update main.py to include new router
echo -e "\n📝 Updating main.py to include task management..."
if ! grep -q "task_management" services/zoe-core/main.py; then
    # Add import
    sed -i '/from routers import/s/$/, task_management/' services/zoe-core/main.py
    # Add router inclusion after other routers
    echo 'app.include_router(task_management.router)' >> services/zoe-core/main.py
fi

# Step 6: Rebuild and restart
echo -e "\n🐳 Rebuilding services..."
docker compose up -d --build zoe-core

# Wait for service to start
echo "Waiting for service to start..."
sleep 10

# Step 7: Test the system
echo -e "\n🧪 Testing task system..."

# Test task list
echo "Testing task list endpoint..."
curl -s http://localhost:8000/api/developer/tasks | jq '.count' && echo "✅ Task list works"

# Test next task
echo "Testing next task endpoint..."
curl -s http://localhost:8000/api/developer/tasks/next | jq '.task_id' && echo "✅ Next task works"

# Step 8: Create quick access scripts
echo -e "\n📄 Creating helper scripts..."

cat > scripts/development/get_next_task.sh << 'BASH'
#!/bin/bash
# Get next task for Cursor or Zack
ASSIGNEE=${1:-Cursor}
curl -s http://localhost:8000/api/developer/tasks/next?assignee=$ASSIGNEE | jq '.'
BASH
chmod +x scripts/development/get_next_task.sh

cat > scripts/development/complete_task.sh << 'BASH'
#!/bin/bash
# Mark task as complete
TASK_ID=$1
RESULT=$2
curl -s -X POST http://localhost:8000/api/developer/tasks/$TASK_ID/complete \
  -H "Content-Type: application/json" \
  -d "{\"result\": \"$RESULT\", \"success\": true}" | jq '.'
BASH
chmod +x scripts/development/complete_task.sh

# Step 9: Final summary
echo -e "\n✅ SETUP COMPLETE!"
echo "=================="
echo ""
echo "📚 Documentation created:"
echo "  - documentation/cursor_context.md"
echo "  - .cursorrules"
echo "  - documentation/cursor_task_plan.md"
echo ""
echo "📊 Task system initialized with $(sqlite3 data/developer_tasks.db 'SELECT COUNT(*) FROM dynamic_tasks WHERE status="pending"') pending tasks"
echo ""
echo "🔌 New API endpoints available:"
echo "  - GET  /api/developer/tasks"
echo "  - GET  /api/developer/tasks/next"
echo "  - POST /api/developer/tasks/{id}/claim"
echo "  - POST /api/developer/tasks/{id}/complete"
echo ""
echo "🚀 Quick commands:"
echo "  Get next task:     ./scripts/development/get_next_task.sh [Cursor|Zack]"
echo "  Complete task:     ./scripts/development/complete_task.sh TASK-XXX 'Result description'"
echo "  View all tasks:    curl http://localhost:8000/api/developer/tasks | jq '.'"
echo ""
echo "📋 Next steps for Cursor:"
echo "  1. Open /home/pi/zoe in Cursor"
echo "  2. Load documentation/cursor_context.md"
echo "  3. Get first task: ./scripts/development/get_next_task.sh Cursor"
echo "  4. Start developing!"
echo ""
echo "🤖 Next steps for Zack:"
echo "  curl http://localhost:8000/api/developer/tasks/next?assignee=Zack"
