from routers.task_executor import TaskExecutor
from routers.plan_generator import PlanGenerator
"""
Dynamic Context-Aware Task Management System
Tasks store intent/requirements and re-analyze at execution time
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
from datetime import datetime
import json
import sqlite3
import hashlib
import logging
import subprocess
import os
from pathlib import Path

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/developer/tasks", tags=["developer-tasks"])

# In-memory task cache
tasks_cache = {}

# Simple WebSocket connection manager for task updates
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        to_remove = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                to_remove.append(connection)
        for conn in to_remove:
            self.disconnect(conn)

manager = ConnectionManager()

class TaskRequirements(BaseModel):
    """What the task needs to achieve (not HOW)"""
    title: str
    objective: str  # What needs to be accomplished
    requirements: List[str]  # Specific requirements
    constraints: List[str]  # What must not be broken
    acceptance_criteria: List[str]  # How to verify success
    priority: str = "medium"
    assigned_to: str = "zack"
    # Time estimation fields
    estimated_duration_minutes: Optional[int] = None
    estimated_duration_hours: Optional[float] = None
    estimated_duration_days: Optional[float] = None
    time_estimation_confidence: Optional[str] = None  # low, medium, high
    actual_duration_minutes: Optional[int] = None
    time_tracking_enabled: bool = True
    
class DynamicTaskPlan(BaseModel):
    """Generated at execution time based on current state"""
    task_id: str
    current_analysis: Dict[str, Any]  # Current system state
    implementation_steps: List[Dict[str, str]]  # Adaptive steps
    integration_points: List[str]  # Where to integrate
    conflict_resolution: List[str]  # How to handle conflicts
    rollback_strategy: Dict[str, Any]
    estimated_impact: str
    
class SystemContext:
    """Gathers current system state for analysis"""
    
    @staticmethod
    def get_current_state() -> Dict[str, Any]:
        """Analyze current system state"""
        state = {
            "timestamp": datetime.now().isoformat(),
            "files": {},
            "endpoints": [],
            "database_schema": {},
            "containers": [],
            "recent_changes": []
        }
        
        try:
            # Get current files
            for root, dirs, files in os.walk("/app"):
                if "routers" in root:
                    for file in files:
                        if file.endswith(".py"):
                            filepath = os.path.join(root, file)
                            with open(filepath, 'r') as f:
                                content = f.read()
                                state["files"][filepath] = {
                                    "size": len(content),
                                    "modified": os.path.getmtime(filepath),
                                    "imports": [line for line in content.split('\n') if 'import' in line][:5],
                                    "endpoints": [line for line in content.split('\n') if '@router.' in line][:5]
                                }
        except Exception as e:
            logger.error(f"Error scanning files: {e}")
            
        try:
            # Get running containers
            result = subprocess.run(
                "docker ps --format '{{.Names}}'",
                shell=True,
                capture_output=True,
                text=True
            )
            state["containers"] = result.stdout.strip().split('\n')
        except:
            pass
            
        try:
            # Get recent git changes (if available)
            result = subprocess.run(
                "cd /app && git log --oneline -5 2>/dev/null || echo 'No git history'",
                shell=True,
                capture_output=True,
                text=True
            )
            state["recent_changes"] = result.stdout.strip().split('\n')
        except:
            pass
            
        return state
    
    @staticmethod
    def detect_conflicts(requirements: List[str], current_state: Dict) -> List[str]:
        """Detect potential conflicts with current system"""
        conflicts = []
        
        # Check for file conflicts
        for req in requirements:
            for filepath in current_state.get("files", {}):
                if any(keyword in req.lower() for keyword in ["modify", "change", "update"]):
                    if any(keyword in filepath.lower() for keyword in req.lower().split()):
                        conflicts.append(f"Potential conflict: {req} may affect {filepath}")
        
        return conflicts

def init_dynamic_tasks_db():
    """Initialize enhanced database for dynamic tasks"""
    conn = sqlite3.connect('/app/data/zoe.db')
    cursor = conn.cursor()
    
    # Create new dynamic tasks table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dynamic_tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            requirements TEXT NOT NULL,  -- JSON array
            constraints TEXT,  -- JSON array
            acceptance_criteria TEXT,  -- JSON array
            priority TEXT DEFAULT 'medium',
            assigned_to TEXT DEFAULT 'zack',
            status TEXT DEFAULT 'pending',
            context_snapshot TEXT,  -- System state when created (for reference)
            last_analysis TEXT,  -- Last execution analysis
            execution_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_executed_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    ''')
    
    # Create execution history table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            execution_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            system_state_before TEXT,  -- JSON
            plan_generated TEXT,  -- JSON
            execution_result TEXT,
            success BOOLEAN,
            changes_made TEXT,  -- JSON list of changes
            FOREIGN KEY (task_id) REFERENCES dynamic_tasks(id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Initialize on import
init_dynamic_tasks_db()

@router.get("/")
async def get_task_system_info():
    """Get information about the dynamic task system"""
    return {
        "system": "Dynamic Context-Aware Task Management",
        "version": "2.0",
        "description": "Tasks adapt to system state at execution time",
        "features": [
            "Stores requirements, not implementations",
            "Re-analyzes system at execution time",
            "Generates adaptive plans",
            "Handles conflicts automatically",
            "Preserves all existing work"
        ],
        "endpoints": {
            "create": "POST /api/developer/tasks/create",
            "list": "GET /api/developer/tasks/list",
            "analyze": "POST /api/developer/tasks/{task_id}/analyze",
            "execute": "POST /api/developer/tasks/{task_id}/execute",
            "complete": "POST /api/developer/tasks/{task_id}/complete",
            "next": "GET /api/developer/tasks/next",
            "claim": "POST /api/developer/tasks/{task_id}/claim",
            "history": "GET /api/developer/tasks/{task_id}/history",
            "ws": "WS /api/developer/tasks/ws/tasks"
        }
    }

@router.post("/create")
async def create_dynamic_task(requirements: TaskRequirements):
    """Create a new dynamic task that stores requirements"""
    
    # Generate task ID
    task_id = hashlib.md5(f"{requirements.title}{datetime.now()}".encode()).hexdigest()[:8]
    
    # Capture current context for reference (not for execution)
    context_snapshot = SystemContext.get_current_state()
    
    # Store in database
    conn = sqlite3.connect('/app/data/zoe.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO dynamic_tasks 
        (id, title, objective, requirements, constraints, acceptance_criteria, 
         priority, assigned_to, status, context_snapshot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        task_id,
        requirements.title,
        requirements.objective,
        json.dumps(requirements.requirements),
        json.dumps(requirements.constraints),
        json.dumps(requirements.acceptance_criteria),
        requirements.priority,
        requirements.assigned_to,
        'pending',
        json.dumps(context_snapshot)
    ))
    
    conn.commit()
    conn.close()
    
    # Cache it
    tasks_cache[task_id] = {
        "id": task_id,
        "title": requirements.title,
        "objective": requirements.objective,
        "status": "pending",
        "created_at": datetime.now().isoformat()
    }
    
    return {
        "task_id": task_id,
        "status": "created",
        "message": f"Dynamic task created. Will analyze system state at execution time.",
        "stored": "requirements_only",
        "execution": "adaptive"
    }

@router.post("/{task_id}/analyze")
async def analyze_for_execution(task_id: str):
    """Analyze current system and generate execution plan"""
    
    # Get task requirements
    conn = sqlite3.connect('/app/data/zoe.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT title, objective, requirements, constraints, acceptance_criteria, context_snapshot
        FROM dynamic_tasks WHERE id = ?
    ''', (task_id,))
    
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    
    title, objective, requirements, constraints, acceptance_criteria, original_context = row
    requirements = json.loads(requirements)
    constraints = json.loads(constraints)
    acceptance_criteria = json.loads(acceptance_criteria)
    original_context = json.loads(original_context)
    
    # Get CURRENT system state
    current_state = SystemContext.get_current_state()
    
    # Detect what has changed since task creation
    changes_detected = []
    for key in current_state.get("files", {}):
        if key not in original_context.get("files", {}):
            changes_detected.append(f"New file: {key}")
        elif current_state["files"][key]["modified"] > original_context.get("files", {}).get(key, {}).get("modified", 0):
            changes_detected.append(f"Modified: {key}")
    
    # Detect potential conflicts
    conflicts = SystemContext.detect_conflicts(requirements, current_state)
    
    # Generate adaptive plan based on CURRENT state
    plan = DynamicTaskPlan(
        task_id=task_id,
        current_analysis={
            "changes_since_creation": changes_detected,
            "conflicts_detected": conflicts,
            "files_to_modify": [],
            "files_to_create": [],
            "integration_needed": True if changes_detected else False
        },
        implementation_steps=[
            {"step": "1", "action": "Backup current system", "command": "cp -r /app /app.backup"},
            {"step": "2", "action": "Analyze integration points", "details": f"Found {len(changes_detected)} changes"},
            {"step": "3", "action": f"Implement: {objective}", "adaptive": "true"},
            {"step": "4", "action": "Run acceptance tests", "criteria": str(acceptance_criteria)},
            {"step": "5", "action": "Verify constraints maintained", "constraints": str(constraints)}
        ],
        integration_points=[f for f in current_state.get("files", {}) if "router" in f],
        conflict_resolution=[f"Handle: {c}" for c in conflicts],
        rollback_strategy={
            "method": "restore_backup",
            "backup_location": "/app.backup",
            "trigger": "test_failure"
        },
        estimated_impact=f"Moderate - {len(changes_detected)} files changed since creation"
    )
    
    # Store the analysis
    cursor.execute('''
        UPDATE dynamic_tasks 
        SET last_analysis = ?, status = 'analyzed'
        WHERE id = ?
    ''', (json.dumps(plan.dict()), task_id))
    
    conn.commit()
    conn.close()
    
    return {
        "task_id": task_id,
        "title": title,
        "objective": objective,
        "system_changes_detected": len(changes_detected),
        "conflicts": conflicts,
        "plan": plan.dict(),
        "ready_to_execute": True,
        "message": "Plan generated based on current system state"
    }

@router.post("/{task_id}/execute")
async def execute_dynamic_task(task_id: str, background_tasks: BackgroundTasks):
    """Execute task with current context awareness"""
    
    # First analyze current state
    analysis = await analyze_for_execution(task_id)
    
    if not analysis["ready_to_execute"]:
        raise HTTPException(status_code=400, detail="Task not ready for execution")
    
    # Log execution start
    conn = sqlite3.connect('/app/data/zoe.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO task_executions (task_id, system_state_before, plan_generated)
        VALUES (?, ?, ?)
    ''', (task_id, json.dumps(SystemContext.get_current_state()), json.dumps(analysis["plan"])))
    
    execution_id = cursor.lastrowid
    
    cursor.execute('''
        UPDATE dynamic_tasks 
        SET status = 'executing', execution_count = execution_count + 1, last_executed_at = ?
        WHERE id = ?
    ''', (datetime.now().isoformat(), task_id))
    
    conn.commit()
    conn.close()
    
    # Execute in background
    background_tasks.add_task(execute_task_async, task_id, execution_id, analysis["plan"])
    
    return {
        "task_id": task_id,
        "execution_id": execution_id,
        "status": "executing",
        "message": "Task execution started with current system context",
        "plan_adapted": True,
        "conflicts_handled": len(analysis.get("conflicts", []))
    }

@router.post("/{task_id}/complete")
async def complete_task(task_id: str):
    """Mark a task as completed and set completed_at timestamp"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dynamic_tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")

    cursor.execute(
        """
        UPDATE dynamic_tasks
        SET status = 'completed', completed_at = ?, last_executed_at = ?
        WHERE id = ?
        """,
        (datetime.now().isoformat(), datetime.now().isoformat(), task_id),
    )
    conn.commit()
    cursor.execute("SELECT * FROM dynamic_tasks WHERE id = ?", (task_id,))
    updated = dict(cursor.fetchone())
    conn.close()

    # Notify websocket listeners
    try:
        await manager.broadcast({"event": "task_completed", "task": updated})
    except Exception:
        pass

    return {"message": f"Task {task_id} marked completed", "task": updated}

async def execute_task_async(task_id: str, execution_id: int, plan: dict):
    """Background task execution with adaptation"""
    from .task_executor import TaskExecutor
    from .plan_generator import PlanGenerator
    import logging
    
    logger = logging.getLogger(__name__)
    executor = TaskExecutor(task_id)
    
    try:
        # Execute the task with full tracking
        # Generate fresh plan based on task requirements
        gen = PlanGenerator()
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        cursor.execute('SELECT objective, requirements, constraints FROM dynamic_tasks WHERE id = ?', (task_id,))
        task_data = cursor.fetchone()
        conn.close()
        
        if task_data:
            fresh_plan = gen.generate_plan({
                'task_id': task_id,
                'objective': task_data[0],
                'requirements': json.loads(task_data[1]),
                'constraints': json.loads(task_data[2]) if task_data[2] else []
            })
            result = executor.execute_plan(fresh_plan)
        else:
            result = {'status': 'failed', 'error': 'Task not found'}
        result = executor.execute_plan(plan)
        
        logger.info(f"Task {task_id} execution completed: {result['status']}")
        
        # Update task status based on result
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        
        if result["status"] == "completed":
            cursor.execute("""
                UPDATE dynamic_tasks 
                SET status = 'completed', completed_at = ?, last_executed_at = ?
                WHERE id = ?
            """, (datetime.now(), datetime.now(), task_id))
            logger.info(f"✅ Task {task_id} completed successfully")
        else:
            cursor.execute("""
                UPDATE dynamic_tasks 
                SET status = 'failed', last_executed_at = ?
                WHERE id = ?
            """, (datetime.now(), task_id))
            logger.error(f"❌ Task {task_id} failed: {result.get('errors', [])}")
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"Task execution failed: {str(e)}")
        
        # Update task status to failed
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE dynamic_tasks 
            SET status = 'failed', last_executed_at = ?
            WHERE id = ?
        """, (datetime.now(), task_id))
        conn.commit()
        conn.close()

@router.get("/list")
async def list_dynamic_tasks(status: Optional[str] = None):
    """List all dynamic tasks"""
    conn = sqlite3.connect('/app/data/zoe.db')
    cursor = conn.cursor()
    
    query = '''
        SELECT id, title, objective, priority, status, created_at, execution_count
        FROM dynamic_tasks
    '''
    
    if status:
        query += f" WHERE status = '{status}'"
    
    query += " ORDER BY created_at DESC"
    
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    
    tasks = [
        {
            "id": row[0],
            "title": row[1],
            "objective": row[2],
            "priority": row[3],
            "status": row[4],
            "created_at": row[5],
            "execution_count": row[6],
            "type": "dynamic"
        }
        for row in rows
    ]
    
    return {
        "tasks": tasks,
        "count": len(tasks),
        "system": "dynamic_context_aware"
    }

@router.get("/{task_id}/history")
async def get_task_execution_history(task_id: str):
    """Get execution history for a task"""
    conn = sqlite3.connect('/app/data/zoe.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, execution_time, success, changes_made
        FROM task_executions
        WHERE task_id = ?
        ORDER BY execution_time DESC
    ''', (task_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    history = [
        {
            "execution_id": row[0],
            "time": row[1],
            "success": row[2],
            "changes": json.loads(row[3]) if row[3] else []
        }
        for row in rows
    ]
    
    return {
        "task_id": task_id,
        "executions": history,
        "total_executions": len(history)
    }

# Backward compatibility - wrap old endpoints
@router.get("/system-info")
async def legacy_system_info():
    """Legacy endpoint for compatibility"""
    return await get_task_system_info()
@router.get("/next")
async def get_next_task(assignee: Optional[str] = None):
    """Get next unclaimed high-priority task"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = """
        SELECT * FROM dynamic_tasks 
        WHERE status = 'pending'
        ORDER BY 
            CASE priority 
                WHEN 'high' THEN 1 
                WHEN 'medium' THEN 2 
                WHEN 'low' THEN 3 
                ELSE 4 
            END,
            created_at ASC
        LIMIT 1
    """
    
    cursor.execute(query)
    task = cursor.fetchone()
    conn.close()
    
    if task:
        # Notify listeners a task was fetched as next (read-only event)
        try:
            await manager.broadcast({"event": "task_next", "task": dict(task)})
        except Exception:
            pass
        return dict(task)
    return {"message": "No pending tasks available"}

@router.post("/{task_id}/claim")
async def claim_task(task_id: str):
    """Claim a task for execution"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE dynamic_tasks 
        SET status = 'analyzing',
            last_executed_at = ?
        WHERE id = ? AND status = 'pending'
    """, (datetime.now().isoformat(), task_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Task not found or already claimed")
    
    conn.commit()
    conn.close()
    
    return {"message": f"Task {task_id} claimed", "status": "analyzing"}

# ================================================================================
# Developer Session Management (Phase 1: beads-inspired)
# ================================================================================

class DeveloperSession(BaseModel):
    """Developer work session for context restoration"""
    session_id: str
    files_changed: List[str] = []
    last_command: Optional[str] = None
    current_task: Optional[str] = None
    next_steps: List[str] = []
    breadcrumbs: List[str] = []
    context_snapshot: Optional[str] = None
    user_id: str = "system"

@router.post("/sessions/save")
async def save_developer_session(session: DeveloperSession):
    """Save current developer session for context restoration"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO developer_sessions 
            (session_id, files_changed, last_command, current_task, next_steps, 
             breadcrumbs, context_snapshot, user_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session.session_id,
            json.dumps(session.files_changed),
            session.last_command,
            session.current_task,
            json.dumps(session.next_steps),
            json.dumps(session.breadcrumbs),
            session.context_snapshot,
            session.user_id,
            datetime.now().isoformat()
        ))
        
        conn.commit()
        return {
            "message": "Session saved successfully",
            "session_id": session.session_id,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error saving session: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save session: {str(e)}")
    finally:
        conn.close()

@router.get("/sessions/restore")
async def restore_developer_session(user_id: str = "system"):
    """Restore most recent developer session"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT session_id, files_changed, last_command, current_task, 
                   next_steps, breadcrumbs, context_snapshot, timestamp
            FROM developer_sessions
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (user_id,))
        
        row = cursor.fetchone()
        if not row:
            return {
                "message": "No previous session found",
                "session": None
            }
        
        return {
            "message": "Session restored",
            "session": {
                "session_id": row[0],
                "files_changed": json.loads(row[1]) if row[1] else [],
                "last_command": row[2],
                "current_task": row[3],
                "next_steps": json.loads(row[4]) if row[4] else [],
                "breadcrumbs": json.loads(row[5]) if row[5] else [],
                "context_snapshot": row[6],
                "timestamp": row[7]
            }
        }
    except Exception as e:
        logger.error(f"Error restoring session: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to restore session: {str(e)}")
    finally:
        conn.close()

@router.get("/sessions/what-was-i-doing")
async def what_was_i_doing(user_id: str = "system"):
    """Get human-readable summary of last work session"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT current_task, files_changed, next_steps, breadcrumbs, timestamp
            FROM developer_sessions
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (user_id,))
        
        row = cursor.fetchone()
        if not row:
            return {
                "summary": "No previous work session found. Starting fresh!",
                "found": False
            }
        
        current_task = row[0]
        files_changed = json.loads(row[1]) if row[1] else []
        next_steps = json.loads(row[2]) if row[2] else []
        breadcrumbs = json.loads(row[3]) if row[3] else []
        timestamp = row[4]
        
        # Build human-readable summary
        summary = f"Last worked: {timestamp}\n\n"
        
        if current_task:
            summary += f"📋 You were working on: {current_task}\n\n"
        
        if files_changed:
            summary += f"📝 Files you modified ({len(files_changed)}):\n"
            for f in files_changed[:5]:  # Show top 5
                summary += f"  • {f}\n"
            if len(files_changed) > 5:
                summary += f"  ... and {len(files_changed) - 5} more\n"
            summary += "\n"
        
        if next_steps:
            summary += f"➡️  Next steps:\n"
            for step in next_steps[:3]:  # Show top 3
                summary += f"  {step}\n"
            summary += "\n"
        
        if breadcrumbs:
            summary += f"🔍 Recent actions ({len(breadcrumbs)}):\n"
            for crumb in breadcrumbs[-5:]:  # Show last 5
                summary += f"  • {crumb}\n"
        
        return {
            "summary": summary,
            "found": True,
            "raw_data": {
                "current_task": current_task,
                "files_changed": files_changed,
                "next_steps": next_steps,
                "breadcrumbs": breadcrumbs,
                "timestamp": timestamp
            }
        }
    except Exception as e:
        logger.error(f"Error retrieving session summary: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get session summary: {str(e)}")
    finally:
        conn.close()

@router.get("/sessions/history")
async def get_session_history(user_id: str = "system", limit: int = 10):
    """Get history of recent developer sessions"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT session_id, current_task, timestamp
            FROM developer_sessions
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (user_id, limit))
        
        sessions = []
        for row in cursor.fetchall():
            sessions.append({
                "session_id": row[0],
                "current_task": row[1],
                "timestamp": row[2]
            })
        
        return {
            "sessions": sessions,
            "count": len(sessions)
        }
    except Exception as e:
        logger.error(f"Error getting session history: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get session history: {str(e)}")
    finally:
        conn.close()

@router.websocket("/ws/tasks")
async def tasks_websocket(websocket: WebSocket):
    """WebSocket for real-time task updates"""
    await manager.connect(websocket)
    try:
        await websocket.send_json({"event": "connected", "message": "Subscribed to task updates"})
        while True:
            # Keep the connection alive; optional ping-pong
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

