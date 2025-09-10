"""
Workflows Management System
Handles automation workflows, triggers, and execution
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json
import os

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_workflows_db():
    """Initialize workflows tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            name TEXT NOT NULL,
            description TEXT,
            trigger_type TEXT NOT NULL,
            trigger_config JSON,
            actions JSON NOT NULL,
            conditions JSON,
            active BOOLEAN DEFAULT TRUE,
            last_run TIMESTAMP,
            next_run TIMESTAMP,
            run_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            error_message TEXT,
            result_data JSON,
            FOREIGN KEY (workflow_id) REFERENCES workflows (id)
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_workflows_active 
        ON workflows(active, user_id)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow 
        ON workflow_runs(workflow_id, started_at)
    """)
    
    conn.commit()
    conn.close()

# Initialize on import
init_workflows_db()

# Request/Response models
class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    trigger_type: str  # "schedule", "event", "manual", "webhook"
    trigger_config: Optional[Dict[str, Any]] = None
    actions: List[Dict[str, Any]]
    conditions: Optional[Dict[str, Any]] = None
    active: Optional[bool] = True
    metadata: Optional[Dict[str, Any]] = None

class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    trigger_type: Optional[str] = None
    trigger_config: Optional[Dict[str, Any]] = None
    actions: Optional[List[Dict[str, Any]]] = None
    conditions: Optional[Dict[str, Any]] = None
    active: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None

class WorkflowResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    trigger_type: str
    trigger_config: Optional[Dict[str, Any]]
    actions: List[Dict[str, Any]]
    conditions: Optional[Dict[str, Any]]
    active: bool
    last_run: Optional[str]
    next_run: Optional[str]
    run_count: int
    success_count: int
    error_count: int
    metadata: Optional[Dict[str, Any]]
    created_at: str
    updated_at: str

@router.get("/")
async def get_workflows(
    active_only: bool = Query(False, description="Show only active workflows"),
    user_id: str = Query("default", description="User ID")
):
    """Get all workflows"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    query = """
        SELECT id, name, description, trigger_type, trigger_config, actions, conditions,
               active, last_run, next_run, run_count, success_count, error_count,
               metadata, created_at, updated_at
        FROM workflows 
        WHERE user_id = ?
    """
    params = [user_id]
    
    if active_only:
        query += " AND active = TRUE"
    
    query += " ORDER BY created_at DESC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    workflows = []
    for row in rows:
        workflows.append({
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "trigger_type": row[3],
            "trigger_config": json.loads(row[4]) if row[4] else None,
            "actions": json.loads(row[5]) if row[5] else [],
            "conditions": json.loads(row[6]) if row[6] else None,
            "active": bool(row[7]),
            "last_run": row[8],
            "next_run": row[9],
            "run_count": row[10],
            "success_count": row[11],
            "error_count": row[12],
            "metadata": json.loads(row[13]) if row[13] else None,
            "created_at": row[14],
            "updated_at": row[15]
        })
    
    return {"workflows": workflows}

@router.post("/")
async def create_workflow(workflow: WorkflowCreate, user_id: str = Query("default")):
    """Create a new workflow"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO workflows (user_id, name, description, trigger_type, trigger_config,
                             actions, conditions, active, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, workflow.name, workflow.description, workflow.trigger_type,
        json.dumps(workflow.trigger_config) if workflow.trigger_config else None,
        json.dumps(workflow.actions), 
        json.dumps(workflow.conditions) if workflow.conditions else None,
        workflow.active,
        json.dumps(workflow.metadata) if workflow.metadata else None
    ))
    
    workflow_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"workflow": {"id": workflow_id, **workflow.dict()}}

@router.get("/{workflow_id}")
async def get_workflow(workflow_id: int, user_id: str = Query("default")):
    """Get a specific workflow"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, name, description, trigger_type, trigger_config, actions, conditions,
               active, last_run, next_run, run_count, success_count, error_count,
               metadata, created_at, updated_at
        FROM workflows 
        WHERE id = ? AND user_id = ?
    """, (workflow_id, user_id))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "trigger_type": row[3],
        "trigger_config": json.loads(row[4]) if row[4] else None,
        "actions": json.loads(row[5]) if row[5] else [],
        "conditions": json.loads(row[6]) if row[6] else None,
        "active": bool(row[7]),
        "last_run": row[8],
        "next_run": row[9],
        "run_count": row[10],
        "success_count": row[11],
        "error_count": row[12],
        "metadata": json.loads(row[13]) if row[13] else None,
        "created_at": row[14],
        "updated_at": row[15]
    }

@router.put("/{workflow_id}")
async def update_workflow(workflow_id: int, workflow_update: WorkflowUpdate, user_id: str = Query("default")):
    """Update a workflow"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if workflow exists
    cursor.execute("SELECT id FROM workflows WHERE id = ? AND user_id = ?", (workflow_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Build update query dynamically
    update_fields = []
    params = []
    
    for field, value in workflow_update.dict(exclude_unset=True).items():
        if field in ["trigger_config", "actions", "conditions", "metadata"] and value is not None:
            update_fields.append(f"{field} = ?")
            params.append(json.dumps(value))
        else:
            update_fields.append(f"{field} = ?")
            params.append(value)
    
    if update_fields:
        update_fields.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([workflow_id, user_id])
        
        query = f"UPDATE workflows SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?"
        cursor.execute(query, params)
        conn.commit()
    
    conn.close()
    
    return {"message": "Workflow updated successfully"}

@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: int, user_id: str = Query("default")):
    """Delete a workflow"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM workflows WHERE id = ? AND user_id = ?", (workflow_id, user_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    conn.commit()
    conn.close()
    
    return {"message": "Workflow deleted successfully"}

@router.post("/{workflow_id}/toggle")
async def toggle_workflow(workflow_id: int, user_id: str = Query("default")):
    """Toggle workflow active status"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE workflows 
        SET active = NOT active, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
    """, (workflow_id, user_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    conn.commit()
    conn.close()
    
    return {"message": "Workflow status toggled successfully"}

@router.post("/{workflow_id}/run")
async def run_workflow(workflow_id: int, user_id: str = Query("default")):
    """Manually run a workflow"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get workflow details
    cursor.execute("""
        SELECT id, name, actions, conditions, active
        FROM workflows 
        WHERE id = ? AND user_id = ?
    """, (workflow_id, user_id))
    
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    if not row[4]:  # active
        conn.close()
        raise HTTPException(status_code=400, detail="Workflow is not active")
    
    # Create workflow run record
    cursor.execute("""
        INSERT INTO workflow_runs (workflow_id, status, started_at)
        VALUES (?, 'running', CURRENT_TIMESTAMP)
    """, (workflow_id,))
    
    run_id = cursor.lastrowid
    
    # Update workflow run count
    cursor.execute("""
        UPDATE workflows 
        SET run_count = run_count + 1, last_run = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (workflow_id,))
    
    conn.commit()
    conn.close()
    
    # TODO: Implement actual workflow execution logic here
    # For now, just simulate success
    try:
        # Simulate workflow execution
        import time
        time.sleep(1)  # Simulate processing time
        
        # Update run status to completed
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE workflow_runs 
            SET status = 'completed', completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (run_id,))
        
        cursor.execute("""
            UPDATE workflows 
            SET success_count = success_count + 1
            WHERE id = ?
        """, (workflow_id,))
        
        conn.commit()
        conn.close()
        
        return {"message": "Workflow executed successfully", "run_id": run_id}
        
    except Exception as e:
        # Update run status to failed
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE workflow_runs 
            SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = ?
            WHERE id = ?
        """, (str(e), run_id))
        
        cursor.execute("""
            UPDATE workflows 
            SET error_count = error_count + 1
            WHERE id = ?
        """, (workflow_id,))
        
        conn.commit()
        conn.close()
        
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {str(e)}")

@router.get("/{workflow_id}/runs")
async def get_workflow_runs(
    workflow_id: int,
    limit: int = Query(20, description="Number of runs to return"),
    user_id: str = Query("default", description="User ID")
):
    """Get workflow execution history"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT wr.id, wr.status, wr.started_at, wr.completed_at, wr.error_message, wr.result_data
        FROM workflow_runs wr
        JOIN workflows w ON wr.workflow_id = w.id
        WHERE w.id = ? AND w.user_id = ?
        ORDER BY wr.started_at DESC
        LIMIT ?
    """, (workflow_id, user_id, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    runs = []
    for row in rows:
        runs.append({
            "id": row[0],
            "status": row[1],
            "started_at": row[2],
            "completed_at": row[3],
            "error_message": row[4],
            "result_data": json.loads(row[5]) if row[5] else None
        })
    
    return {"runs": runs}
