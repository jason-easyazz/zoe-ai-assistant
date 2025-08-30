#!/bin/bash
# COMPLETE_TASKS_SYSTEM.sh
# Location: scripts/development/complete_tasks_system.sh
# Purpose: Complete Zack task system Phase 2 - Full CRUD operations and UI

set -e

echo "🎯 Completing Zack Task System Phase 2"
echo "======================================"
echo ""
echo "This script will:"
echo "  1. Check current system state"
echo "  2. Complete tasks.py with full CRUD operations"
echo "  3. Create task conversation UI"
echo "  4. Integrate with Zack (developer AI)"
echo "  5. Add approval workflow"
echo ""
echo "Press Enter to continue or Ctrl+C to abort..."
read

cd /home/pi/zoe

# Step 1: Check current state
echo -e "\n📊 STEP 1: Checking Current State"
echo "=================================="

# Check GitHub for latest
echo "📍 Pulling latest from GitHub..."
git pull

# Check progress file
echo -e "\n📄 Current progress:"
if [ -f "TASK_SYSTEM_PROGRESS.md" ]; then
    cat TASK_SYSTEM_PROGRESS.md | tail -20
else
    echo "No progress file found, creating..."
    cat > TASK_SYSTEM_PROGRESS.md << 'EOF'
# Task System Progress
Created: $(date)

## Phase 2 Status:
- [ ] Complete tasks.py endpoints
- [ ] Create task conversation UI
- [ ] Integrate with Zack
- [ ] Add approval workflow
EOF
fi

# Verify what's working
echo -e "\n✅ Verifying working components:"
echo "Developer status:"
curl -s http://localhost:8000/api/developer/status | jq '.' || echo "❌ Developer API not responding"

echo -e "\nTasks test endpoint:"
curl -s http://localhost:8000/api/tasks/test | jq '.' || echo "❌ Tasks API not registered"

echo -e "\nCurrent tasks.py size:"
docker exec zoe-core wc -l /app/routers/tasks.py 2>/dev/null || echo "tasks.py not found"

# Step 2: Backup current state
echo -e "\n📦 STEP 2: Creating Backup"
echo "=========================="
mkdir -p backups/$(date +%Y%m%d_%H%M%S)
cp -r services backups/$(date +%Y%m%d_%H%M%S)/

# Step 3: Complete tasks.py with full CRUD operations
echo -e "\n🔧 STEP 3: Creating Complete tasks.py"
echo "======================================"

cat > services/zoe-core/routers/tasks.py << 'TASKS_PY'
"""
Task Management System for Developer Workflow
Integrates with Zack (developer AI) for code generation
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json
import os
import uuid

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# Pydantic models
class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    task_type: str = "feature"  # feature, bug, refactor, test, docs
    priority: str = "medium"  # low, medium, high, critical
    assigned_to: str = "zack"
    metadata: Optional[Dict[str, Any]] = {}

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class TaskConversation(BaseModel):
    task_id: str
    message: str
    role: str = "developer"  # developer, zack, system

class CodeApproval(BaseModel):
    task_id: str
    approved: bool
    comments: Optional[str] = None

# Database initialization
def init_db():
    """Initialize task-related database tables"""
    conn = sqlite3.connect("/app/data/zoe.db")
    cursor = conn.cursor()
    
    # Task conversations table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            code_snippet TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Tasks table with full fields
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            task_type TEXT DEFAULT 'feature',
            status TEXT DEFAULT 'pending',
            priority TEXT DEFAULT 'medium',
            assigned_to TEXT DEFAULT 'zack',
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            approved BOOLEAN DEFAULT 0,
            code_generated TEXT,
            implementation_path TEXT
        )
    """)
    
    # Code approvals table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS code_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            approved BOOLEAN,
            comments TEXT,
            approved_by TEXT DEFAULT 'developer',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks(task_id)
        )
    """)
    
    conn.commit()
    conn.close()

# Initialize database on import
init_db()

# Helper functions
def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect("/app/data/zoe.db")
    conn.row_factory = sqlite3.Row
    return conn

def task_to_dict(row):
    """Convert SQLite row to dictionary"""
    if row is None:
        return None
    return {
        "task_id": row["task_id"],
        "title": row["title"],
        "description": row["description"],
        "task_type": row["task_type"],
        "status": row["status"],
        "priority": row["priority"],
        "assigned_to": row["assigned_to"],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "approved": bool(row["approved"]),
        "code_generated": row["code_generated"],
        "implementation_path": row["implementation_path"]
    }

# API Endpoints
@router.get("/test")
async def test_endpoint():
    """Test endpoint to verify router is working"""
    return {"status": "working", "message": "Tasks API is operational"}

@router.post("/")
async def create_task(task: TaskCreate):
    """Create a new task for Zack to work on"""
    task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO tasks (
                task_id, title, description, task_type, 
                priority, assigned_to, metadata, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (
            task_id,
            task.title,
            task.description,
            task.task_type,
            task.priority,
            task.assigned_to,
            json.dumps(task.metadata) if task.metadata else None
        ))
        
        # Add initial conversation entry
        cursor.execute("""
            INSERT INTO task_conversations (task_id, role, message)
            VALUES (?, 'system', ?)
        """, (task_id, f"Task created: {task.title}"))
        
        conn.commit()
        
        # If assigned to Zack, trigger AI processing
        if task.assigned_to == "zack":
            # This would trigger Zack to analyze the task
            cursor.execute("""
                INSERT INTO task_conversations (task_id, role, message)
                VALUES (?, 'system', 'Task assigned to Zack for analysis')
            """, (task_id,))
            conn.commit()
        
        return {
            "task_id": task_id,
            "status": "created",
            "message": f"Task {task_id} created successfully"
        }
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@router.get("/")
async def list_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    task_type: Optional[str] = None,
    limit: int = 50
):
    """List all tasks with optional filters"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM tasks WHERE 1=1"
    params = []
    
    if status:
        query += " AND status = ?"
        params.append(status)
    if priority:
        query += " AND priority = ?"
        params.append(priority)
    if task_type:
        query += " AND task_type = ?"
        params.append(task_type)
    
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    tasks = [task_to_dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return {
        "tasks": tasks,
        "count": len(tasks),
        "filters": {
            "status": status,
            "priority": priority,
            "task_type": task_type
        }
    }

@router.get("/{task_id}")
async def get_task(task_id: str):
    """Get a specific task with its conversation history"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get task details
    cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    task = task_to_dict(cursor.fetchone())
    
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Get conversation history
    cursor.execute("""
        SELECT * FROM task_conversations 
        WHERE task_id = ? 
        ORDER BY timestamp ASC
    """, (task_id,))
    
    conversations = []
    for row in cursor.fetchall():
        conversations.append({
            "id": row["id"],
            "role": row["role"],
            "message": row["message"],
            "code_snippet": row["code_snippet"],
            "timestamp": row["timestamp"]
        })
    
    conn.close()
    
    return {
        "task": task,
        "conversation": conversations,
        "conversation_count": len(conversations)
    }

@router.put("/{task_id}")
async def update_task(task_id: str, update: TaskUpdate):
    """Update task details"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if task exists
    cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Build update query
    updates = []
    params = []
    
    if update.title is not None:
        updates.append("title = ?")
        params.append(update.title)
    if update.description is not None:
        updates.append("description = ?")
        params.append(update.description)
    if update.status is not None:
        updates.append("status = ?")
        params.append(update.status)
        if update.status == "completed":
            updates.append("completed_at = CURRENT_TIMESTAMP")
    if update.priority is not None:
        updates.append("priority = ?")
        params.append(update.priority)
    if update.metadata is not None:
        updates.append("metadata = ?")
        params.append(json.dumps(update.metadata))
    
    updates.append("updated_at = CURRENT_TIMESTAMP")
    
    query = f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?"
    params.append(task_id)
    
    try:
        cursor.execute(query, params)
        conn.commit()
        
        # Add conversation entry about update
        cursor.execute("""
            INSERT INTO task_conversations (task_id, role, message)
            VALUES (?, 'system', ?)
        """, (task_id, f"Task updated: {', '.join([k for k in update.dict() if update.dict()[k] is not None])}"))
        conn.commit()
        
        return {"status": "updated", "task_id": task_id}
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@router.post("/{task_id}/conversation")
async def add_conversation(task_id: str, conversation: TaskConversation):
    """Add a message to task conversation"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if task exists
    cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    
    try:
        # Check if message contains code
        code_snippet = None
        if "```" in conversation.message:
            # Extract code snippet
            import re
            code_match = re.search(r"```[\w]*\n(.*?)```", conversation.message, re.DOTALL)
            if code_match:
                code_snippet = code_match.group(1)
        
        cursor.execute("""
            INSERT INTO task_conversations (task_id, role, message, code_snippet)
            VALUES (?, ?, ?, ?)
        """, (task_id, conversation.role, conversation.message, code_snippet))
        
        conn.commit()
        
        return {
            "status": "added",
            "task_id": task_id,
            "conversation_id": cursor.lastrowid
        }
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@router.post("/{task_id}/generate-code")
async def generate_code_for_task(task_id: str, background_tasks: BackgroundTasks):
    """Trigger Zack to generate code for this task"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get task details
    cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    task = task_to_dict(cursor.fetchone())
    
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Create prompt for Zack
    prompt = f"""
    Task: {task['title']}
    Type: {task['task_type']}
    Description: {task['description'] or 'No description provided'}
    Priority: {task['priority']}
    
    Generate production-ready code for this task.
    Include error handling, tests, and documentation.
    """
    
    # Add to conversation
    cursor.execute("""
        INSERT INTO task_conversations (task_id, role, message)
        VALUES (?, 'system', 'Code generation requested from Zack')
    """, (task_id,))
    
    # Update task status
    cursor.execute("""
        UPDATE tasks 
        SET status = 'in_progress', updated_at = CURRENT_TIMESTAMP
        WHERE task_id = ?
    """, (task_id,))
    
    conn.commit()
    conn.close()
    
    # This would trigger the actual Zack AI integration
    # For now, we'll return a placeholder
    return {
        "status": "generation_started",
        "task_id": task_id,
        "message": "Zack is generating code for this task"
    }

@router.post("/{task_id}/approve")
async def approve_code(task_id: str, approval: CodeApproval):
    """Approve or reject generated code"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if task exists
    cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    
    try:
        # Add approval record
        cursor.execute("""
            INSERT INTO code_approvals (task_id, approved, comments)
            VALUES (?, ?, ?)
        """, (task_id, approval.approved, approval.comments))
        
        # Update task
        if approval.approved:
            cursor.execute("""
                UPDATE tasks 
                SET approved = 1, status = 'approved', updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
            """, (task_id,))
            message = "Code approved for implementation"
        else:
            cursor.execute("""
                UPDATE tasks 
                SET status = 'needs_revision', updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
            """, (task_id,))
            message = f"Code rejected: {approval.comments or 'No comments'}"
        
        # Add to conversation
        cursor.execute("""
            INSERT INTO task_conversations (task_id, role, message)
            VALUES (?, 'developer', ?)
        """, (task_id, message))
        
        conn.commit()
        
        return {
            "status": "approved" if approval.approved else "rejected",
            "task_id": task_id,
            "message": message
        }
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@router.delete("/{task_id}")
async def delete_task(task_id: str):
    """Delete a task and its conversation history"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if task exists
    cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    
    try:
        # Delete conversations first (foreign key)
        cursor.execute("DELETE FROM task_conversations WHERE task_id = ?", (task_id,))
        cursor.execute("DELETE FROM code_approvals WHERE task_id = ?", (task_id,))
        cursor.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        
        conn.commit()
        
        return {
            "status": "deleted",
            "task_id": task_id,
            "message": f"Task {task_id} and its history deleted"
        }
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@router.get("/stats/summary")
async def get_task_stats():
    """Get task statistics summary"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    stats = {}
    
    # Total tasks by status
    cursor.execute("""
        SELECT status, COUNT(*) as count 
        FROM tasks 
        GROUP BY status
    """)
    stats["by_status"] = {row["status"]: row["count"] for row in cursor.fetchall()}
    
    # Total tasks by priority
    cursor.execute("""
        SELECT priority, COUNT(*) as count 
        FROM tasks 
        GROUP BY priority
    """)
    stats["by_priority"] = {row["priority"]: row["count"] for row in cursor.fetchall()}
    
    # Total tasks by type
    cursor.execute("""
        SELECT task_type, COUNT(*) as count 
        FROM tasks 
        GROUP BY task_type
    """)
    stats["by_type"] = {row["task_type"]: row["count"] for row in cursor.fetchall()}
    
    # Recent activity
    cursor.execute("""
        SELECT COUNT(*) as count 
        FROM tasks 
        WHERE created_at > datetime('now', '-7 days')
    """)
    stats["created_last_week"] = cursor.fetchone()["count"]
    
    # Approval rate
    cursor.execute("SELECT COUNT(*) as total FROM tasks WHERE status = 'approved'")
    approved = cursor.fetchone()["count"]
    cursor.execute("SELECT COUNT(*) as total FROM tasks WHERE code_generated IS NOT NULL")
    generated = cursor.fetchone()["count"]
    stats["approval_rate"] = f"{(approved/generated*100) if generated > 0 else 0:.1f}%"
    
    conn.close()
    
    return stats
TASKS_PY

echo "✅ Complete tasks.py created with full CRUD operations"

# Step 4: Reorganize Developer Section
echo -e "\n🎨 STEP 4: Reorganizing Developer Section"
echo "========================================"

# Move current index.html to chat.html
echo "Moving current developer index to chat.html..."
cp services/zoe-ui/dist/developer/index.html services/zoe-ui/dist/developer/chat.html

# Create new dashboard index.html
cat > services/zoe-ui/dist/developer/index.html << 'DASHBOARD_HTML'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zoe Developer Dashboard</title>
    <link rel="stylesheet" href="../css/glass.css">
    <style>
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }
        
        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            padding: 20px;
            max-width: 1400px;
            margin: 0 auto;
        }
        
        .dashboard-card {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.2);
            transition: transform 0.3s;
        }
        
        .dashboard-card:hover {
            transform: translateY(-5px);
        }
        
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        
        .card-title {
            font-size: 1.2em;
            font-weight: 600;
            color: white;
        }
        
        .card-icon {
            font-size: 1.5em;
        }
        
        .stat-value {
            font-size: 2em;
            font-weight: bold;
            color: white;
            margin: 10px 0;
        }
        
        .stat-label {
            font-size: 0.9em;
            color: rgba(255, 255, 255, 0.7);
        }
        
        .nav-header {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            padding: 15px 20px;
            margin-bottom: 20px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .nav-links {
            display: flex;
            gap: 20px;
            align-items: center;
        }
        
        .nav-link {
            color: white;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 8px;
            transition: background 0.3s;
        }
        
        .nav-link:hover {
            background: rgba(255, 255, 255, 0.2);
        }
        
        .nav-link.active {
            background: rgba(255, 255, 255, 0.3);
        }
        
        .task-list-item {
            background: rgba(255, 255, 255, 0.05);
            padding: 10px;
            margin: 5px 0;
            border-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            color: white;
        }
        
        .priority-badge {
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.8em;
        }
        
        .priority-critical { background: #ff4444; }
        .priority-high { background: #ff8844; }
        .priority-medium { background: #ffcc44; }
        .priority-low { background: #44ff44; }
        
        .quick-action-btn {
            background: rgba(255, 255, 255, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.3);
            color: white;
            padding: 12px 20px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s;
            width: 100%;
            margin: 5px 0;
        }
        
        .quick-action-btn:hover {
            background: rgba(255, 255, 255, 0.3);
            transform: translateX(5px);
        }
    </style>
</head>
<body>
    <div class="nav-header">
        <div class="nav-links">
            <a href="index.html" class="nav-link active">📊 Dashboard</a>
            <a href="chat.html" class="nav-link">💬 Chat with Zack</a>
            <a href="tasks.html" class="nav-link">📋 Tasks</a>
            <a href="monitor.html" class="nav-link">📈 Monitor</a>
            <a href="settings.html" class="nav-link">⚙️ Settings</a>
            <a href="../index.html" class="nav-link">🏠 Main App</a>
        </div>
    </div>
    
    <div class="dashboard-grid">
        <!-- Task Statistics -->
        <div class="dashboard-card">
            <div class="card-header">
                <span class="card-title">Task Overview</span>
                <span class="card-icon">📋</span>
            </div>
            <div class="stat-value" id="totalTasks">0</div>
            <div class="stat-label">Total Tasks</div>
            <div style="display: flex; gap: 10px; margin-top: 15px;">
                <div>
                    <div style="color: #44ff44;">Active: <span id="activeTasks">0</span></div>
                </div>
                <div>
                    <div style="color: #ffcc44;">Pending: <span id="pendingTasks">0</span></div>
                </div>
                <div>
                    <div style="color: #888;">Completed: <span id="completedTasks">0</span></div>
                </div>
            </div>
        </div>
        
        <!-- System Status -->
        <div class="dashboard-card">
            <div class="card-header">
                <span class="card-title">System Status</span>
                <span class="card-icon">🟢</span>
            </div>
            <div id="systemStatus">
                <div class="task-list-item">
                    <span>Zoe Core</span>
                    <span style="color: #44ff44;">● Running</span>
                </div>
                <div class="task-list-item">
                    <span>Zack AI</span>
                    <span style="color: #44ff44;">● Connected</span>
                </div>
                <div class="task-list-item">
                    <span>Database</span>
                    <span style="color: #44ff44;">● Healthy</span>
                </div>
            </div>
        </div>
        
        <!-- Recent Activity -->
        <div class="dashboard-card">
            <div class="card-header">
                <span class="card-title">Recent Activity</span>
                <span class="card-icon">⚡</span>
            </div>
            <div id="recentActivity">
                <div class="task-list-item">
                    <span>System initialized</span>
                    <span style="font-size: 0.8em;">Just now</span>
                </div>
            </div>
        </div>
        
        <!-- High Priority Tasks -->
        <div class="dashboard-card">
            <div class="card-header">
                <span class="card-title">High Priority Tasks</span>
                <span class="card-icon">🔥</span>
            </div>
            <div id="highPriorityTasks">
                <div style="color: rgba(255,255,255,0.5); text-align: center; padding: 20px;">
                    No high priority tasks
                </div>
            </div>
        </div>
        
        <!-- Quick Actions -->
        <div class="dashboard-card">
            <div class="card-header">
                <span class="card-title">Quick Actions</span>
                <span class="card-icon">⚡</span>
            </div>
            <button class="quick-action-btn" onclick="window.location.href='chat.html'">
                💬 Start Chat with Zack
            </button>
            <button class="quick-action-btn" onclick="createQuickTask()">
                ➕ Create New Task
            </button>
            <button class="quick-action-btn" onclick="window.location.href='tasks.html'">
                📋 View All Tasks
            </button>
            <button class="quick-action-btn" onclick="runSystemCheck()">
                🔍 System Health Check
            </button>
        </div>
        
        <!-- Code Generation Stats -->
        <div class="dashboard-card">
            <div class="card-header">
                <span class="card-title">Code Generation</span>
                <span class="card-icon">🤖</span>
            </div>
            <div class="stat-value" id="codeGenerated">0</div>
            <div class="stat-label">Scripts Generated Today</div>
            <div style="margin-top: 15px;">
                <div style="color: #44ff44;">Approved: <span id="approvedCode">0</span></div>
                <div style="color: #ff8844;">Pending Review: <span id="pendingCode">0</span></div>
            </div>
        </div>
    </div>
    
    <script>
        // Load dashboard data
        async function loadDashboard() {
            try {
                // Load task statistics
                const response = await fetch('/api/tasks/stats/summary');
                const stats = await response.json();
                
                document.getElementById('totalTasks').textContent = 
                    (stats.by_status?.pending || 0) + 
                    (stats.by_status?.in_progress || 0) + 
                    (stats.by_status?.completed || 0);
                
                document.getElementById('activeTasks').textContent = stats.by_status?.in_progress || 0;
                document.getElementById('pendingTasks').textContent = stats.by_status?.pending || 0;
                document.getElementById('completedTasks').textContent = stats.by_status?.completed || 0;
                
                // Load high priority tasks
                const tasksResponse = await fetch('/api/tasks?priority=high&limit=5');
                const tasksData = await tasksResponse.json();
                
                const highPriorityEl = document.getElementById('highPriorityTasks');
                if (tasksData.tasks && tasksData.tasks.length > 0) {
                    highPriorityEl.innerHTML = tasksData.tasks.map(task => `
                        <div class="task-list-item" onclick="window.location.href='tasks.html#${task.task_id}'">
                            <span>${task.title}</span>
                            <span class="priority-badge priority-${task.priority}">${task.priority}</span>
                        </div>
                    `).join('');
                }
            } catch (error) {
                console.error('Failed to load dashboard:', error);
            }
        }
        
        function createQuickTask() {
            const title = prompt('Task title:');
            if (!title) return;
            
            fetch('/api/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: title,
                    task_type: 'feature',
                    priority: 'medium'
                })
            }).then(() => {
                window.location.href = 'tasks.html';
            });
        }
        
        function runSystemCheck() {
            alert('Running system check... (This would trigger a real check)');
        }
        
        // Load on page load
        window.onload = loadDashboard;
        
        // Refresh every 30 seconds
        setInterval(loadDashboard, 30000);
    </script>
</body>
</html>
DASHBOARD_HTML

echo "✅ Developer dashboard created"

# Step 5: Create Tasks UI in developer folder
echo -e "\n📋 STEP 5: Creating Tasks UI in Developer Section"
echo "=================================================="

cat > services/zoe-ui/dist/developer/tasks.html << 'TASKS_HTML'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zoe Developer - Task Management</title>
    <link rel="stylesheet" href="../css/glass.css">
    <style>
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
        
        .nav-header {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            padding: 15px 20px;
            margin-bottom: 20px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .nav-links {
            display: flex;
            gap: 20px;
            align-items: center;
        }
        
        .nav-link {
            color: white;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 8px;
            transition: background 0.3s;
        }
        
        .nav-link:hover {
            background: rgba(255, 255, 255, 0.2);
        }
        
        .nav-link.active {
            background: rgba(255, 255, 255, 0.3);
        }
        
        .task-grid {
            display: grid;
            grid-template-columns: 350px 1fr;
            gap: 20px;
            height: calc(100vh - 100px);
            padding: 0 20px 20px 20px;
        }
        
        .task-sidebar {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            padding: 20px;
            overflow-y: auto;
        }
        
        .filter-tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        
        .filter-tab {
            padding: 8px 12px;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 8px;
            color: white;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .filter-tab.active {
            background: rgba(255, 255, 255, 0.3);
        }
        
        .task-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .task-item {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 15px;
            cursor: pointer;
            transition: all 0.3s;
            border-left: 4px solid transparent;
        }
        
        .task-item:hover {
            background: rgba(255, 255, 255, 0.15);
            transform: translateX(5px);
        }
        
        .task-item.active {
            background: rgba(100, 200, 255, 0.2);
            border: 1px solid rgba(100, 200, 255, 0.5);
        }
        
        .priority-critical { border-left-color: #ff4444; }
        .priority-high { border-left-color: #ff8844; }
        .priority-medium { border-left-color: #ffcc44; }
        .priority-low { border-left-color: #44ff44; }
        
        .task-conversation {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            padding: 20px;
            display: flex;
            flex-direction: column;
        }
        
        .conversation-header {
            margin-bottom: 20px;
            padding-bottom: 20px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .conversation-messages {
            flex: 1;
            overflow-y: auto;
            margin-bottom: 20px;
        }
        
        .message {
            margin-bottom: 15px;
            padding: 10px;
            border-radius: 8px;
        }
        
        .message.developer {
            background: rgba(100, 200, 255, 0.1);
            margin-right: 20%;
        }
        
        .message.zack {
            background: rgba(255, 200, 100, 0.1);
            margin-left: 20%;
        }
        
        .message.system {
            background: rgba(200, 200, 200, 0.1);
            text-align: center;
            font-style: italic;
            font-size: 0.9em;
        }
        
        .code-block {
            background: #1e1e1e;
            border-radius: 8px;
            padding: 10px;
            margin: 10px 0;
            font-family: 'Courier New', monospace;
            overflow-x: auto;
        }
        
        .conversation-input {
            display: flex;
            gap: 10px;
        }
        
        .conversation-input input {
            flex: 1;
            padding: 12px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.2);
            background: rgba(255, 255, 255, 0.05);
            color: white;
        }
        
        .btn-primary {
            padding: 12px 24px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border: none;
            border-radius: 8px;
            color: white;
            cursor: pointer;
        }
        
        .task-actions {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
        }
        
        .btn-action {
            padding: 8px 16px;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.2);
            background: rgba(255, 255, 255, 0.1);
            color: white;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .btn-action:hover {
            background: rgba(255, 255, 255, 0.2);
        }
        
        .btn-approve { background: rgba(100, 255, 100, 0.2); }
        .btn-reject { background: rgba(255, 100, 100, 0.2); }
        
        .empty-state {
            text-align: center;
            padding: 40px;
            color: rgba(255, 255, 255, 0.6);
        }
        
        .empty-state h2 {
            color: rgba(255, 255, 255, 0.8);
            margin-bottom: 10px;
        }
    </style>
</head>
<body>
    <div class="nav-header">
        <div class="nav-links">
            <a href="index.html" class="nav-link">📊 Dashboard</a>
            <a href="chat.html" class="nav-link">💬 Chat with Zack</a>
            <a href="tasks.html" class="nav-link active">📋 Tasks</a>
            <a href="monitor.html" class="nav-link">📈 Monitor</a>
            <a href="settings.html" class="nav-link">⚙️ Settings</a>
            <a href="../index.html" class="nav-link">🏠 Main App</a>
        </div>
    </div>
    
    <div class="task-grid">
        <!-- Task List Sidebar -->
        <div class="task-sidebar">
            <h2 style="color: white; margin-bottom: 20px;">📋 Tasks</h2>
            
            <button class="btn-primary" onclick="createTask()" style="width: 100%; margin-bottom: 20px;">
                + Create New Task
            </button>
            
            <!-- Filter Tabs -->
            <div class="filter-tabs">
                <div class="filter-tab active" onclick="filterTasks('all')">All</div>
                <div class="filter-tab" onclick="filterTasks('active')">Active</div>
                <div class="filter-tab" onclick="filterTasks('completed')">Completed</div>
                <div class="filter-tab" onclick="filterTasks('archived')">Archived</div>
            </div>
            
            <!-- Task List -->
            <div class="task-list" id="taskList">
                <!-- Tasks will be loaded here -->
            </div>
        </div>
        
        <!-- Task Conversation Area -->
        <div class="task-conversation">
            <div id="emptyState" class="empty-state">
                <h2>Select a Task</h2>
                <p>Choose a task from the left to view its conversation and details</p>
                <p style="margin-top: 20px;">Or create a new task to get started</p>
            </div>
            
            <div id="taskContent" style="display: none;">
                <div class="conversation-header" id="taskHeader">
                    <!-- Task header will be loaded here -->
                </div>
                
                <div class="task-actions" id="taskActions">
                    <button class="btn-action" onclick="generateCode()">🤖 Generate Code</button>
                    <button class="btn-action btn-approve" onclick="approveCode()">✅ Approve</button>
                    <button class="btn-action btn-reject" onclick="rejectCode()">❌ Reject</button>
                    <button class="btn-action" onclick="deleteTask()">🗑️ Delete</button>
                </div>
                
                <div class="conversation-messages" id="conversationMessages">
                    <!-- Conversation will be loaded here -->
                </div>
                
                <div class="conversation-input">
                    <input type="text" id="messageInput" placeholder="Add a comment or instruction..." 
                           onkeypress="if(event.key === 'Enter') sendMessage()">
                    <button class="btn-primary" onclick="sendMessage()">Send</button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // Use relative paths for API
        const API_BASE = '/api';
        let currentTaskId = null;
        let currentFilter = 'all';
        
        // Load tasks on page load
        window.onload = () => {
            loadTasks();
            // Check for task ID in URL hash
            if (window.location.hash) {
                const taskId = window.location.hash.substring(1);
                loadTask(taskId);
            }
        };
        
        async function loadTasks() {
            try {
                let url = `${API_BASE}/tasks`;
                if (currentFilter !== 'all') {
                    url += `?status=${currentFilter}`;
                }
                
                const response = await fetch(url);
                const data = await response.json();
                
                const taskList = document.getElementById('taskList');
                
                if (data.tasks.length === 0) {
                    taskList.innerHTML = '<div style="color: rgba(255,255,255,0.5); text-align: center; padding: 20px;">No tasks found</div>';
                    return;
                }
                
                taskList.innerHTML = data.tasks.map(task => `
                    <div class="task-item priority-${task.priority} ${task.task_id === currentTaskId ? 'active' : ''}" 
                         onclick="loadTask('${task.task_id}')">
                        <div style="font-weight: bold; color: white;">${task.task_id}</div>
                        <div style="color: rgba(255,255,255,0.9);">${task.title}</div>
                        <div style="font-size: 0.9em; color: rgba(255,255,255,0.6); margin-top: 5px;">
                            ${task.task_type} • ${task.status} • ${task.priority}
                        </div>
                    </div>
                `).join('');
            } catch (error) {
                console.error('Failed to load tasks:', error);
            }
        }
        
        async function loadTask(taskId) {
            currentTaskId = taskId;
            window.location.hash = taskId;
            
            // Hide empty state, show content
            document.getElementById('emptyState').style.display = 'none';
            document.getElementById('taskContent').style.display = 'block';
            
            try {
                const response = await fetch(`${API_BASE}/tasks/${taskId}`);
                const data = await response.json();
                
                // Update header
                document.getElementById('taskHeader').innerHTML = `
                    <h2 style="color: white;">${data.task.task_id}: ${data.task.title}</h2>
                    <p style="color: rgba(255,255,255,0.8);">${data.task.description || 'No description'}</p>
                    <p style="color: rgba(255,255,255,0.6);">Status: ${data.task.status} | Priority: ${data.task.priority} | Type: ${data.task.task_type}</p>
                `;
                
                // Load conversation
                const messagesEl = document.getElementById('conversationMessages');
                messagesEl.innerHTML = data.conversation.map(msg => `
                    <div class="message ${msg.role}">
                        <div style="font-weight: bold; margin-bottom: 5px; color: white;">
                            ${msg.role.toUpperCase()} - ${new Date(msg.timestamp).toLocaleString()}
                        </div>
                        <div style="color: rgba(255,255,255,0.9);">
                            ${msg.message}
                            ${msg.code_snippet ? `<div class="code-block">${escapeHtml(msg.code_snippet)}</div>` : ''}
                        </div>
                    </div>
                `).join('');
                
                messagesEl.scrollTop = messagesEl.scrollHeight;
                
                // Update task list to show active
                loadTasks();
            } catch (error) {
                console.error('Failed to load task:', error);
                alert('Failed to load task');
            }
        }
        
        function filterTasks(filter) {
            currentFilter = filter;
            
            // Update active tab
            document.querySelectorAll('.filter-tab').forEach(tab => {
                tab.classList.remove('active');
            });
            event.target.classList.add('active');
            
            loadTasks();
        }
        
        async function createTask() {
            const title = prompt('Task title:');
            if (!title) return;
            
            const description = prompt('Task description (optional):');
            const taskType = prompt('Task type (feature/bug/refactor/test/docs):', 'feature');
            const priority = prompt('Priority (low/medium/high/critical):', 'medium');
            
            try {
                const response = await fetch(`${API_BASE}/tasks`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: title,
                        description: description,
                        task_type: taskType,
                        priority: priority
                    })
                });
                
                const data = await response.json();
                loadTasks();
                loadTask(data.task_id);
            } catch (error) {
                alert('Failed to create task: ' + error.message);
            }
        }
        
        // Add task from chat functionality
        window.addTaskFromChat = function(chatMessage, chatResponse) {
            // This function can be called from chat.html
            fetch(`${API_BASE}/tasks`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: chatMessage.substring(0, 50) + '...',
                    description: `User: ${chatMessage}\n\nZack: ${chatResponse}`,
                    task_type: 'feature',
                    priority: 'medium'
                })
            }).then(response => response.json())
              .then(data => {
                  alert(`Task created: ${data.task_id}`);
                  window.location.href = `tasks.html#${data.task_id}`;
              });
        };
        
        // Load tasks on page load
        window.onload = () => {
            loadTasks();
        };
        
        async function loadTasks() {
            try {
                const response = await fetch(`${API_BASE}/api/tasks`);
                const data = await response.json();
                
                const taskList = document.getElementById('taskList');
                taskList.innerHTML = '';
                
                data.tasks.forEach(task => {
                    const taskEl = document.createElement('div');
                    taskEl.className = `task-item priority-${task.priority}`;
                    if (task.task_id === currentTaskId) {
                        taskEl.classList.add('active');
                    }
                    
                    taskEl.innerHTML = `
                        <div style="font-weight: bold;">${task.task_id}</div>
                        <div>${task.title}</div>
                        <div style="font-size: 0.9em; opacity: 0.7;">
                            ${task.task_type} • ${task.status} • ${task.priority}
                        </div>
                    `;
                    
                    taskEl.onclick = () => loadTask(task.task_id);
                    taskList.appendChild(taskEl);
                });
            } catch (error) {
                console.error('Failed to load tasks:', error);
            }
        }
        
        async function loadTask(taskId) {
            currentTaskId = taskId;
            
            try {
                const response = await fetch(`${API_BASE}/api/tasks/${taskId}`);
                const data = await response.json();
                
                // Update header
                document.getElementById('taskHeader').innerHTML = `
                    <h2>${data.task.task_id}: ${data.task.title}</h2>
                    <p>${data.task.description || 'No description'}</p>
                    <p>Status: ${data.task.status} | Priority: ${data.task.priority}</p>
                `;
                
                // Show actions and input
                document.getElementById('taskActions').style.display = 'flex';
                document.getElementById('conversationInput').style.display = 'flex';
                
                // Load conversation
                const messagesEl = document.getElementById('conversationMessages');
                messagesEl.innerHTML = '';
                
                data.conversation.forEach(msg => {
                    const msgEl = document.createElement('div');
                    msgEl.className = `message ${msg.role}`;
                    
                    let content = msg.message;
                    if (msg.code_snippet) {
                        content += `<div class="code-block">${escapeHtml(msg.code_snippet)}</div>`;
                    }
                    
                    msgEl.innerHTML = `
                        <div style="font-weight: bold; margin-bottom: 5px;">
                            ${msg.role.toUpperCase()} - ${new Date(msg.timestamp).toLocaleString()}
                        </div>
                        <div>${content}</div>
                    `;
                    
                    messagesEl.appendChild(msgEl);
                });
                
                messagesEl.scrollTop = messagesEl.scrollHeight;
                
                // Reload task list to update status
                loadTasks();
            } catch (error) {
                console.error('Failed to load task:', error);
            }
        }
        
        async function createTask() {
            const title = prompt('Task title:');
            if (!title) return;
            
            const description = prompt('Task description (optional):');
            const taskType = prompt('Task type (feature/bug/refactor/test/docs):', 'feature');
            const priority = prompt('Priority (low/medium/high/critical):', 'medium');
