"""
Collections Router - Manage collections of items (people, memories, etc.)
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json
import os
from auth_integration import validate_session, AuthenticatedSession

router = APIRouter(prefix="/api/collections", tags=["collections"])

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

class CollectionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    layout_config: Optional[Dict[str, Any]] = {}

class CollectionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    layout_config: Optional[Dict[str, Any]] = None

def init_collections_db():
    """Collections table already exists - no initialization needed"""
    pass

@router.get("/", response_model=Dict[str, Any])
async def get_collections(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get user's collections"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM collections
            WHERE user_id = ?
            ORDER BY updated_at DESC
        """, (user_id,))
        
        collections = []
        for row in cursor.fetchall():
            collections.append({
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "layout_config": json.loads(row["layout_config"]) if row["layout_config"] else {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            })
        
        conn.close()
        return {"collections": collections, "count": len(collections)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/", response_model=Dict[str, Any])
async def create_collection(
    collection_data: CollectionCreate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Create a new collection"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO collections (user_id, name, description, layout_config)
            VALUES (?, ?, ?, ?)
        """, (user_id, collection_data.name, collection_data.description, 
              json.dumps(collection_data.layout_config)))
        
        collection_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "collection_id": collection_id,
            "message": "Collection created successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{collection_id}", response_model=Dict[str, Any])
async def update_collection(
    collection_id: int,
    collection_data: CollectionUpdate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update a collection"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        if collection_data.name is not None:
            updates.append("name = ?")
            params.append(collection_data.name)
        if collection_data.description is not None:
            updates.append("description = ?")
            params.append(collection_data.description)
        if collection_data.layout_config is not None:
            updates.append("layout_config = ?")
            params.append(json.dumps(collection_data.layout_config))
        
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.extend([collection_id, user_id])
            
            query = f"UPDATE collections SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
            cursor.execute(query, params)
            
            if cursor.rowcount == 0:
                conn.close()
                raise HTTPException(status_code=404, detail="Collection not found")
            
            conn.commit()
        
        conn.close()
        return {"message": "Collection updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{collection_id}", response_model=Dict[str, Any])
async def delete_collection(
    collection_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete a collection"""
    user_id = session.user_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            DELETE FROM collections WHERE id = ? AND user_id = ?
        """, (collection_id, user_id))
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Collection not found")
        
        conn.commit()
        conn.close()
        
        return {"message": "Collection deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Initialize database on startup
init_collections_db()

