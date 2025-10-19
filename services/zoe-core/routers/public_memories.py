"""
Public Memories Endpoint
========================

A public endpoint for memories that works without authentication
for the web interface
"""

from fastapi import APIRouter, Query
from typing import Dict, Any, Optional
import sqlite3
import json

router = APIRouter(prefix="/api/public-memories", tags=["public-memories"])

@router.get("/")
async def get_public_memories(
    type: str = Query(..., description="Type: people, projects, or notes"),
    user_id: str = Query("default", description="User ID")
):
    """Get memories by type without authentication (for web interface)"""
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        
        if type == "people":
            cursor.execute("""
                SELECT id, name, relationship, birthday, phone, email, address, notes,
                       avatar_url, tags, metadata, created_at, updated_at
                FROM people 
                WHERE user_id = ?
                ORDER BY name
            """, (user_id,))
            
            items = []
            for row in cursor.fetchall():
                items.append({
                    "id": row[0],
                    "name": row[1],
                    "relationship": row[2],
                    "birthday": row[3],
                    "phone": row[4],
                    "email": row[5],
                    "address": row[6],
                    "notes": row[7],
                    "avatar_url": row[8],
                    "tags": json.loads(row[9]) if row[9] else None,
                    "metadata": json.loads(row[10]) if row[10] else None,
                    "created_at": row[11],
                    "updated_at": row[12]
                })
        
        elif type == "projects":
            cursor.execute("""
                SELECT id, name, description, status, start_date, end_date, priority,
                       tags, metadata, created_at, updated_at
                FROM projects 
                WHERE user_id = ?
                ORDER BY created_at DESC
            """, (user_id,))
            
            items = []
            for row in cursor.fetchall():
                items.append({
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "status": row[3],
                    "start_date": row[4],
                    "end_date": row[5],
                    "priority": row[6],
                    "tags": json.loads(row[7]) if row[7] else None,
                    "metadata": json.loads(row[8]) if row[8] else None,
                    "created_at": row[9],
                    "updated_at": row[10]
                })
        
        elif type == "notes":
            cursor.execute("""
                SELECT id, title, content, category, tags, metadata, created_at, updated_at
                FROM notes 
                WHERE user_id = ?
                ORDER BY created_at DESC
            """, (user_id,))
            
            items = []
            for row in cursor.fetchall():
                items.append({
                    "id": row[0],
                    "title": row[1],
                    "content": row[2],
                    "category": row[3],
                    "tags": json.loads(row[4]) if row[4] else None,
                    "metadata": json.loads(row[5]) if row[5] else None,
                    "created_at": row[6],
                    "updated_at": row[7]
                })
        
        conn.close()
        
        return {"memories": items}
        
    except Exception as e:
        return {"error": str(e), "memories": []}
