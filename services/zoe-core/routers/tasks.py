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
    stats["created_last_week"] = cursor.fetchone()[0]
    
    # Approval rate
    cursor.execute("SELECT COUNT(*) as total FROM tasks WHERE status = 'approved'")
    approved = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) as total FROM tasks WHERE code_generated IS NOT NULL")
    generated = cursor.fetchone()[0]
    stats["approval_rate"] = f"{(approved/generated*100) if generated > 0 else 0:.1f}%"
    
    conn.close()
    
    return stats
