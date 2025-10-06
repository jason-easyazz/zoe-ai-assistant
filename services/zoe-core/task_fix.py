"""Task endpoint fix to ensure JSON responses"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sqlite3
import uuid
from typing import Optional

router = APIRouter(prefix="/api/developer")

class DevelopmentTask(BaseModel):
    title: str
    description: str
    type: str = "feature"
    priority: str = "medium"

@router.post("/tasks", response_class=JSONResponse)
async def create_task(task: DevelopmentTask):
    """Create task with guaranteed JSON response"""
    try:
        task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
        
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        
        # Ensure table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                title TEXT,
                description TEXT,
                type TEXT,
                priority TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute(
            "INSERT INTO tasks (task_id, title, description, type, priority) VALUES (?, ?, ?, ?, ?)",
            (task_id, task.title, task.description, task.type, task.priority)
        )
        
        conn.commit()
        conn.close()
        
        return {"task_id": task_id, "status": "created", "title": task.title}
        
    except Exception as e:
        return {"error": str(e), "status": "failed"}
