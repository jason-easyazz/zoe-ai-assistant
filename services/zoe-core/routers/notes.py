"""
Notes Router - Simple note-taking functionality
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json
import os
from auth_integration import validate_session, AuthenticatedSession

router = APIRouter(prefix="/api/notes", tags=["notes"])

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

class NoteCreate(BaseModel):
    title: Optional[str] = "Untitled Note"
    content: str
    category: Optional[str] = "general"
    tags: Optional[List[str]] = []

class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None

def init_notes_db():
    """Initialize notes table"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            title TEXT DEFAULT 'Untitled Note',
            content TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            tags JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_deleted BOOLEAN DEFAULT 0
        )
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_category ON notes(category)")
    
    conn.commit()
    conn.close()

@router.get("/", response_model=Dict[str, Any])
async def get_notes(
    session: AuthenticatedSession = Depends(validate_session),
    category: Optional[str] = None,
    limit: int = 100
):
    """Get user's notes"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if category:
            cursor.execute("""
                SELECT * FROM notes
                WHERE user_id = ? AND category = ? AND is_deleted = 0
                ORDER BY updated_at DESC
                LIMIT ?
            """, (user_id, category, limit))
        else:
            cursor.execute("""
                SELECT * FROM notes
                WHERE user_id = ? AND is_deleted = 0
                ORDER BY updated_at DESC
                LIMIT ?
            """, (user_id, limit))
        
        notes = []
        for row in cursor.fetchall():
            notes.append({
                "id": row["id"],
                "title": row["title"],
                "content": row["content"],
                "category": row["category"],
                "tags": json.loads(row["tags"]) if row["tags"] else [],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            })
        
        conn.close()
        return {"notes": notes, "count": len(notes)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/", response_model=Dict[str, Any])
async def create_note(
    note_data: NoteCreate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Create a new note"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO notes (user_id, title, content, category, tags)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, note_data.title, note_data.content, note_data.category, 
              json.dumps(note_data.tags)))
        
        note_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "note_id": note_id,
            "message": "Note created successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{note_id}", response_model=Dict[str, Any])
async def update_note(
    note_id: int,
    note_data: NoteUpdate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update a note"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Build dynamic update query
        updates = []
        params = []
        
        if note_data.title is not None:
            updates.append("title = ?")
            params.append(note_data.title)
        if note_data.content is not None:
            updates.append("content = ?")
            params.append(note_data.content)
        if note_data.category is not None:
            updates.append("category = ?")
            params.append(note_data.category)
        if note_data.tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(note_data.tags))
        
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.extend([note_id, user_id])
            
            query = f"UPDATE notes SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
            cursor.execute(query, params)
            
            if cursor.rowcount == 0:
                conn.close()
                raise HTTPException(status_code=404, detail="Note not found")
            
            conn.commit()
        
        conn.close()
        return {"message": "Note updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{note_id}", response_model=Dict[str, Any])
async def delete_note(
    note_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete a note (soft delete)"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE notes SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
        """, (note_id, user_id))
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Note not found")
        
        conn.commit()
        conn.close()
        
        return {"message": "Note deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Initialize database on startup
init_notes_db()

