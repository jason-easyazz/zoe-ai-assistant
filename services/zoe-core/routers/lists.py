"""
Lists Management System
Supports: Shopping, Bucket, Tasks, Custom
With work/personal separation
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json
import os

router = APIRouter(prefix="/api/lists", tags=["lists"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_lists_db():
    """Initialize lists tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            list_type TEXT NOT NULL,
            list_category TEXT DEFAULT 'personal',
            name TEXT NOT NULL,
            items JSON,
            metadata JSON,
            shared_with JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_lists_type 
        ON lists(list_type, list_category, user_id)
    """)
    
    conn.commit()
    conn.close()

# Initialize on import
init_lists_db()

# Request/Response models
class ListItem(BaseModel):
    id: Optional[int] = None
    text: str
    completed: bool = False
    priority: Optional[str] = "medium"
    category: Optional[str] = "personal"
    metadata: Optional[Dict[str, Any]] = {}

class ListCreate(BaseModel):
    list_type: str  # shopping, bucket, tasks, custom
    category: str = "personal"  # personal, work
    name: str
    items: List[ListItem] = []

class ListUpdate(BaseModel):
    name: Optional[str] = None
    items: Optional[List[ListItem]] = None
    category: Optional[str] = None

@router.get("/types")
async def get_list_types():
    """Get available list types and categories"""
    return {
        "types": ["shopping", "bucket", "tasks", "custom"],
        "categories": ["personal", "work"],
        "templates": {
            "shopping": ["Groceries", "Home Supplies", "Electronics"],
            "bucket": ["Travel", "Skills", "Experiences"],
            "tasks": ["Daily", "Weekly", "Projects"],
            "custom": []
        }
    }

@router.get("/{list_type}")
async def get_lists(
    list_type: str,
    category: Optional[str] = Query(None, description="Filter by category (personal/work)"),
    user_id: str = Query("default", description="User ID")
):
    """Get all lists of a specific type"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if category:
        cursor.execute("""
            SELECT id, name, items, list_category, created_at, updated_at
            FROM lists 
            WHERE list_type = ? AND list_category = ? AND user_id = ?
            ORDER BY updated_at DESC
        """, (list_type, category, user_id))
    else:
        cursor.execute("""
            SELECT id, name, items, list_category, created_at, updated_at
            FROM lists 
            WHERE list_type = ? AND user_id = ?
            ORDER BY updated_at DESC
        """, (list_type, user_id))
    
    rows = cursor.fetchall()
    conn.close()
    
    lists = []
    for row in rows:
        lists.append({
            "id": row[0],
            "name": row[1],
            "items": json.loads(row[2]) if row[2] else [],
            "category": row[3],
            "created_at": row[4],
            "updated_at": row[5]
        })
    
    return {"lists": lists, "count": len(lists)}

@router.post("/{list_type}")
async def create_list(
    list_type: str,
    list_data: ListCreate,
    user_id: str = Query("default")
):
    """Create a new list"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    items_json = json.dumps([item.dict() for item in list_data.items])
    
    cursor.execute("""
        INSERT INTO lists (user_id, list_type, list_category, name, items)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, list_type, list_data.category, list_data.name, items_json))
    
    list_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {
        "id": list_id,
        "message": f"{list_type.title()} list created",
        "name": list_data.name,
        "category": list_data.category
    }

@router.put("/{list_type}/{list_id}")
async def update_list(
    list_type: str,
    list_id: int,
    update_data: ListUpdate,
    user_id: str = Query("default")
):
    """Update a list"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Build update query dynamically
    updates = []
    params = []
    
    if update_data.name:
        updates.append("name = ?")
        params.append(update_data.name)
    
    if update_data.items is not None:
        updates.append("items = ?")
        params.append(json.dumps([item.dict() for item in update_data.items]))
    
    if update_data.category:
        updates.append("list_category = ?")
        params.append(update_data.category)
    
    updates.append("updated_at = CURRENT_TIMESTAMP")
    
    params.extend([list_id, list_type, user_id])
    
    cursor.execute(f"""
        UPDATE lists 
        SET {', '.join(updates)}
        WHERE id = ? AND list_type = ? AND user_id = ?
    """, params)
    
    conn.commit()
    conn.close()
    
    return {"message": "List updated", "id": list_id}

@router.delete("/{list_type}/{list_id}")
async def delete_list(
    list_type: str,
    list_id: int,
    user_id: str = Query("default")
):
    """Delete a list"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        DELETE FROM lists 
        WHERE id = ? AND list_type = ? AND user_id = ?
    """, (list_id, list_type, user_id))
    
    conn.commit()
    conn.close()
    
    return {"message": "List deleted", "id": list_id}

@router.post("/{list_type}/{list_id}/share")
async def share_list(
    list_type: str,
    list_id: int,
    share_with: List[str],
    user_id: str = Query("default")
):
    """Share a list with other users"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE lists 
        SET shared_with = ?
        WHERE id = ? AND list_type = ? AND user_id = ?
    """, (json.dumps(share_with), list_id, list_type, user_id))
    
    conn.commit()
    conn.close()
    
    return {"message": "List shared", "shared_with": share_with}
