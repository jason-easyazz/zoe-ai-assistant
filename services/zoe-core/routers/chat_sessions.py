"""
Chat Sessions Management
Implements session persistence for AG-UI chat interface
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json
import os
from auth_integration import validate_session, AuthenticatedSession

router = APIRouter(prefix="/api/chat/sessions", tags=["chat-sessions"])

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

class SessionCreate(BaseModel):
    title: Optional[str] = "New Chat"
    initial_message: Optional[str] = None

class SessionUpdate(BaseModel):
    title: Optional[str] = None

class MessageCreate(BaseModel):
    session_id: str
    role: str  # 'user' or 'assistant'
    content: str
    metadata: Optional[Dict[str, Any]] = None

def init_sessions_db():
    """Initialize chat sessions tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Chat sessions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT DEFAULT 'New Chat',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_count INTEGER DEFAULT 0,
            metadata JSON
        )
    """)
    
    # Chat messages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON chat_sessions(user_id, updated_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id, created_at ASC)")
    
    conn.commit()
    conn.close()

@router.post("/", response_model=Dict[str, Any])
async def create_session(
    session_data: SessionCreate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Create a new chat session"""
    user_id = session.user_id
    try:
        session_id = f"session_{int(datetime.now().timestamp() * 1000)}"
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO chat_sessions (id, user_id, title, metadata)
            VALUES (?, ?, ?, ?)
        """, (session_id, user_id, session_data.title, json.dumps({})))
        
        # Add initial message if provided
        if session_data.initial_message:
            cursor.execute("""
                INSERT INTO chat_messages (session_id, role, content)
                VALUES (?, ?, ?)
            """, (session_id, 'user', session_data.initial_message))
            
            cursor.execute("""
                UPDATE chat_sessions SET message_count = 1 WHERE id = ?
            """, (session_id,))
        
        conn.commit()
        conn.close()
        
        return {
            "session_id": session_id,
            "message": "Session created successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/", response_model=Dict[str, Any])
async def get_sessions(
    session: AuthenticatedSession = Depends(validate_session),
    limit: int = Query(50, description="Maximum number of sessions to return")
):
    """Get user's chat sessions"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM chat_sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
        """, (user_id, limit))
        
        sessions = []
        for row in cursor.fetchall():
            sessions.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "title": row["title"],
                "message_count": row["message_count"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            })
        
        conn.close()
        return {"sessions": sessions, "count": len(sessions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{session_id}/messages/", response_model=Dict[str, Any])
@router.get("/{session_id}/messages", response_model=Dict[str, Any])
async def get_session_messages(
    session_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get all messages in a session"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Verify session belongs to user
        cursor.execute("""
            SELECT * FROM chat_sessions WHERE id = ? AND user_id = ?
        """, (session_id, user_id))
        
        session = cursor.fetchone()
        if not session:
            conn.close()
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Get messages
        cursor.execute("""
            SELECT * FROM chat_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
        """, (session_id,))
        
        messages = []
        for row in cursor.fetchall():
            messages.append({
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
                "created_at": row["created_at"]
            })
        
        conn.close()
        return {
            "session": {
                "id": session["id"],
                "title": session["title"],
                "message_count": session["message_count"]
            },
            "messages": messages
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{session_id}/messages/", response_model=Dict[str, Any])
@router.post("/{session_id}/messages", response_model=Dict[str, Any])
async def add_message(
    session_id: str,
    message: MessageCreate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Add a message to a session"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verify session exists and belongs to user
        cursor.execute("""
            SELECT id FROM chat_sessions WHERE id = ? AND user_id = ?
        """, (session_id, user_id))
        
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Add message
        cursor.execute("""
            INSERT INTO chat_messages (session_id, role, content, metadata)
            VALUES (?, ?, ?, ?)
        """, (session_id, message.role, message.content, json.dumps(message.metadata) if message.metadata else None))
        
        message_id = cursor.lastrowid
        
        # Update session message count and timestamp
        cursor.execute("""
            UPDATE chat_sessions 
            SET message_count = message_count + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (session_id,))
        
        conn.commit()
        conn.close()
        
        return {
            "message_id": message_id,
            "message": "Message added successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{session_id}", response_model=Dict[str, Any])
async def update_session(
    session_id: str,
    update: SessionUpdate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update session (e.g., rename)"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        if update.title:
            cursor.execute("""
                UPDATE chat_sessions 
                SET title = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
            """, (update.title, session_id, user_id))
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Session not found")
        
        conn.commit()
        conn.close()
        
        return {"message": "Session updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{session_id}", response_model=Dict[str, Any])
async def delete_session(
    session_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete a session and all its messages"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            DELETE FROM chat_sessions WHERE id = ? AND user_id = ?
        """, (session_id, user_id))
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Session not found")
        
        conn.commit()
        conn.close()
        
        return {"message": "Session deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Initialize database on startup
init_sessions_db()

