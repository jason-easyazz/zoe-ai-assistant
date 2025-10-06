"""
Aider Router - Web API for Aider interactions
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict
import sys
import os

sys.path.append('/app')
from aider_service import aider_service

router = APIRouter(prefix="/api/aider", tags=["aider"])

class AiderSessionCreate(BaseModel):
    task_id: Optional[str] = None
    files: Optional[List[str]] = []
    
class AiderMessage(BaseModel):
    session_id: str
    message: str
    auto_execute: bool = False

class TaskLink(BaseModel):
    session_id: str
    task_id: str

@router.post("/session")
async def create_aider_session(request: AiderSessionCreate):
    """Create new Aider coding session"""
    session_id = await aider_service.create_session(
        task_id=request.task_id,
        files=request.files
    )
    
    return {
        "session_id": session_id,
        "status": "created",
        "model": aider_service.sessions[session_id]["model"]
    }

@router.post("/chat")
async def chat_with_aider(request: AiderMessage):
    """Send message to Aider and get response"""
    response = await aider_service.send_message(
        session_id=request.session_id,
        message=request.message,
        auto_execute=request.auto_execute
    )
    
    return response

@router.get("/session/{session_id}/history")
async def get_session_history(session_id: str):
    """Get conversation history for an Aider session"""
    history = await aider_service.get_session_history(session_id)
    return {"session_id": session_id, "messages": history}

@router.post("/link-task")
async def link_session_to_task(request: TaskLink):
    """Link an Aider session to a task"""
    await aider_service.link_to_task(request.session_id, request.task_id)
    return {"status": "linked", "session_id": request.session_id, "task_id": request.task_id}

@router.get("/sessions")
async def list_aider_sessions():
    """List all Aider sessions"""
    import sqlite3
    conn = sqlite3.connect("/app/data/aider_conversations.db")
    c = conn.cursor()
    
    c.execute("""
        SELECT session_id, task_id, created_at, last_active, status
        FROM aider_sessions
        ORDER BY last_active DESC
        LIMIT 20
    """)
    
    sessions = []
    for row in c.fetchall():
        sessions.append({
            "session_id": row[0],
            "task_id": row[1],
            "created_at": row[2],
            "last_active": row[3],
            "status": row[4]
        })
    
    conn.close()
    return {"sessions": sessions}
