#!/usr/bin/env python3
"""
Zoe Collections Service - Dedicated service for collections and tiles management
Extracted from memories router with enhanced visual layout and content curation
"""

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import json
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.append('/app')

app = FastAPI(title="Zoe Collections Service", version="1.0.0")

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Initialize collections service
class CollectionsService:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize collections database tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Collections table (unified schema)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS collections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                name TEXT NOT NULL,
                description TEXT,
                layout_config JSON DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name)
            )
        """)
        
        # Tiles table (unified schema)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                content TEXT,
                position_x INTEGER DEFAULT 0,
                position_y INTEGER DEFAULT 0,
                width INTEGER DEFAULT 200,
                height INTEGER DEFAULT 150,
                tile_type TEXT DEFAULT 'text',
                metadata JSON DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Collection layouts for advanced visual management
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS collection_layouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                layout_name TEXT NOT NULL,
                layout_config JSON NOT NULL,
                is_default BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(collection_id, layout_name)
            )
        """)
        
        # Content curation rules
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curation_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                rule_name TEXT NOT NULL,
                rule_type TEXT NOT NULL, -- 'auto_tag', 'auto_position', 'content_filter'
                rule_config JSON NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
    
    def _connect_db(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

# Initialize service
collections_service = CollectionsService(DB_PATH)

# Pydantic models
class CollectionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    layout_config: Optional[Dict[str, Any]] = None

class CollectionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    layout_config: Optional[Dict[str, Any]] = None

class TileCreate(BaseModel):
    collection_id: int
    title: str
    content: Optional[str] = None
    position_x: Optional[int] = 0
    position_y: Optional[int] = 0
    width: Optional[int] = 200
    height: Optional[int] = 150
    tile_type: Optional[str] = "text"
    metadata: Optional[Dict[str, Any]] = None

class TileUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    position_x: Optional[int] = None
    position_y: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    tile_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class LayoutCreate(BaseModel):
    collection_id: int
    layout_name: str
    layout_config: Dict[str, Any]
    is_default: Optional[bool] = False

class CurationRuleCreate(BaseModel):
    collection_id: int
    rule_name: str
    rule_type: str  # 'auto_tag', 'auto_position', 'content_filter'
    rule_config: Dict[str, Any]
    is_active: Optional[bool] = True

# API Endpoints
@app.get("/")
async def root():
    """Service health check"""
    return {"service": "Zoe Collections Service", "status": "healthy", "version": "1.0.0"}

@app.get("/collections")
async def get_collections(user_id: str = Query("default", description="User ID")):
    """Get all collections for a user"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, description, layout_config, created_at, updated_at
            FROM collections 
            WHERE user_id = ?
            ORDER BY created_at DESC
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

@app.get("/collections/{collection_id}")
async def get_collection(collection_id: int, user_id: str = Query("default", description="User ID")):
    """Get a specific collection by ID"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, description, layout_config, created_at, updated_at
            FROM collections 
            WHERE id = ? AND user_id = ?
        """, (collection_id, user_id))
        
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Collection not found")
        
        collection = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "layout_config": json.loads(row["layout_config"]) if row["layout_config"] else {},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }
        
        conn.close()
        return {"collection": collection}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/collections")
async def create_collection(collection: CollectionCreate, user_id: str = Query("default", description="User ID")):
    """Create a new collection"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Check if collection already exists for this user
        cursor.execute("SELECT name FROM collections WHERE user_id = ? AND name = ?", (user_id, collection.name))
        if cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Collection '{collection.name}' already exists")
        
        # Create collection
        cursor.execute("""
            INSERT INTO collections (user_id, name, description, layout_config)
            VALUES (?, ?, ?, ?)
        """, (
            user_id, collection.name, collection.description,
            json.dumps(collection.layout_config or {})
        ))
        
        collection_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {"collection": {"id": collection_id, "name": collection.name}}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/collections/{collection_id}")
async def update_collection(collection_id: int, collection: CollectionUpdate, user_id: str = Query("default", description="User ID")):
    """Update a collection"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Check if collection exists
        cursor.execute("SELECT name FROM collections WHERE id = ? AND user_id = ?", (collection_id, user_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Collection not found")
        
        # Update collection
        update_fields = []
        update_values = []
        
        if collection.name is not None:
            update_fields.append("name = ?")
            update_values.append(collection.name)
        
        if collection.description is not None:
            update_fields.append("description = ?")
            update_values.append(collection.description)
        
        if collection.layout_config is not None:
            update_fields.append("layout_config = ?")
            update_values.append(json.dumps(collection.layout_config))
        
        if update_fields:
            update_fields.append("updated_at = CURRENT_TIMESTAMP")
            update_values.extend([collection_id, user_id])
            
            cursor.execute(f"""
                UPDATE collections 
                SET {', '.join(update_fields)}
                WHERE id = ? AND user_id = ?
            """, update_values)
            
            conn.commit()
        
        conn.close()
        return {"message": "Collection updated successfully", "collection_id": collection_id}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/collections/{collection_id}")
async def delete_collection(collection_id: int, user_id: str = Query("default", description="User ID")):
    """Delete a collection and all its tiles"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Check if collection exists
        cursor.execute("SELECT name FROM collections WHERE id = ? AND user_id = ?", (collection_id, user_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Collection not found")
        
        # Delete collection (tiles will be deleted automatically due to CASCADE)
        cursor.execute("DELETE FROM collections WHERE id = ? AND user_id = ?", (collection_id, user_id))
        
        conn.commit()
        conn.close()
        
        return {"message": f"Collection '{row['name']}' deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/collections/{collection_id}/tiles")
async def get_tiles(collection_id: int, user_id: str = Query("default", description="User ID")):
    """Get all tiles in a collection"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Verify collection exists and belongs to user
        cursor.execute("SELECT id FROM collections WHERE id = ? AND user_id = ?", (collection_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Collection not found")
        
        cursor.execute("""
            SELECT id, title, content, position_x, position_y, width, height,
                   tile_type, metadata, created_at, updated_at
            FROM tiles 
            WHERE collection_id = ?
            ORDER BY position_y, position_x
        """, (collection_id,))
        
        tiles = []
        for row in cursor.fetchall():
            tiles.append({
                "id": row["id"],
                "title": row["title"],
                "content": row["content"],
                "position_x": row["position_x"],
                "position_y": row["position_y"],
                "width": row["width"],
                "height": row["height"],
                "tile_type": row["tile_type"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            })
        
        conn.close()
        return {"tiles": tiles, "count": len(tiles)}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tiles")
async def create_tile(tile: TileCreate, user_id: str = Query("default", description="User ID")):
    """Create a new tile in a collection"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Verify collection exists and belongs to user
        cursor.execute("SELECT id FROM collections WHERE id = ? AND user_id = ?", (tile.collection_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=400, detail="Collection not found")
        
        # Create tile
        cursor.execute("""
            INSERT INTO tiles (collection_id, title, content, position_x, position_y,
                             width, height, tile_type, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tile.collection_id, tile.title, tile.content,
            tile.position_x, tile.position_y, tile.width, tile.height,
            tile.tile_type, json.dumps(tile.metadata or {})
        ))
        
        tile_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {"tile": {"id": tile_id, "title": tile.title}}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/tiles/{tile_id}")
async def update_tile(tile_id: int, tile: TileUpdate, user_id: str = Query("default", description="User ID")):
    """Update a tile"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Check if tile exists
        cursor.execute("SELECT title FROM tiles WHERE id = ?", (tile_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Tile not found")
    
        # Update tile
        update_fields = []
        update_values = []
        
        if tile.title is not None:
            update_fields.append("title = ?")
            update_values.append(tile.title)
        
        if tile.content is not None:
            update_fields.append("content = ?")
            update_values.append(tile.content)
        
        if tile.position_x is not None:
            update_fields.append("position_x = ?")
            update_values.append(tile.position_x)
        
        if tile.position_y is not None:
            update_fields.append("position_y = ?")
            update_values.append(tile.position_y)
        
        if tile.width is not None:
            update_fields.append("width = ?")
            update_values.append(tile.width)
        
        if tile.height is not None:
            update_fields.append("height = ?")
            update_values.append(tile.height)
        
        if tile.tile_type is not None:
            update_fields.append("tile_type = ?")
            update_values.append(tile.tile_type)
        
        if tile.metadata is not None:
            update_fields.append("metadata = ?")
            update_values.append(json.dumps(tile.metadata))
        
        if update_fields:
            update_fields.append("updated_at = CURRENT_TIMESTAMP")
            update_values.extend([tile_id])
            
            cursor.execute(f"""
                UPDATE tiles 
                SET {', '.join(update_fields)}
                WHERE id = ?
            """, update_values)
            
            conn.commit()
        
        conn.close()
        return {"message": "Tile updated successfully", "tile_id": tile_id}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/tiles/{tile_id}")
async def delete_tile(tile_id: int, user_id: str = Query("default", description="User ID")):
    """Delete a tile"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Check if tile exists
        cursor.execute("SELECT title FROM tiles WHERE id = ?", (tile_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Tile not found")
        
        # Delete tile
        cursor.execute("DELETE FROM tiles WHERE id = ?", (tile_id,))
        
        conn.commit()
        conn.close()
        
        return {"message": f"Tile '{row['title']}' deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/layouts")
async def create_layout(layout: LayoutCreate, user_id: str = Query("default", description="User ID")):
    """Create a layout for a collection"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Verify collection exists and belongs to user
        cursor.execute("SELECT id FROM collections WHERE id = ? AND user_id = ?", (layout.collection_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=400, detail="Collection not found")
        
        # Check if layout name already exists for this collection
        cursor.execute("SELECT id FROM collection_layouts WHERE collection_id = ? AND layout_name = ?", 
                      (layout.collection_id, layout.layout_name))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail=f"Layout '{layout.layout_name}' already exists for this collection")
        
        # If this is set as default, unset other defaults for this collection
        if layout.is_default:
            cursor.execute("UPDATE collection_layouts SET is_default = FALSE WHERE collection_id = ?", 
                          (layout.collection_id,))
        
        # Create layout
        cursor.execute("""
            INSERT INTO collection_layouts (collection_id, user_id, layout_name, layout_config, is_default)
            VALUES (?, ?, ?, ?, ?)
        """, (
            layout.collection_id, user_id, layout.layout_name,
            json.dumps(layout.layout_config), layout.is_default
        ))
        
        layout_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {"layout": {"id": layout_id, "layout_name": layout.layout_name}}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/collections/{collection_id}/layouts")
async def get_collection_layouts(collection_id: int, user_id: str = Query("default", description="User ID")):
    """Get all layouts for a collection"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Verify collection exists and belongs to user
        cursor.execute("SELECT id FROM collections WHERE id = ? AND user_id = ?", (collection_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Collection not found")
        
        cursor.execute("""
            SELECT id, layout_name, layout_config, is_default, created_at
            FROM collection_layouts 
            WHERE collection_id = ? AND user_id = ?
            ORDER BY is_default DESC, created_at DESC
        """, (collection_id, user_id))
        
        layouts = []
        for row in cursor.fetchall():
            layouts.append({
                "id": row["id"],
                "layout_name": row["layout_name"],
                "layout_config": json.loads(row["layout_config"]),
                "is_default": bool(row["is_default"]),
                "created_at": row["created_at"]
            })
        
        conn.close()
        return {"layouts": layouts, "count": len(layouts)}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/curation-rules")
async def create_curation_rule(rule: CurationRuleCreate, user_id: str = Query("default", description="User ID")):
    """Create a content curation rule for a collection"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Verify collection exists and belongs to user
        cursor.execute("SELECT id FROM collections WHERE id = ? AND user_id = ?", (rule.collection_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=400, detail="Collection not found")
        
        # Create curation rule
        cursor.execute("""
            INSERT INTO curation_rules (collection_id, user_id, rule_name, rule_type, rule_config, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            rule.collection_id, user_id, rule.rule_name, rule.rule_type,
            json.dumps(rule.rule_config), rule.is_active
        ))
        
        rule_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {"rule": {"id": rule_id, "rule_name": rule.rule_name}}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/collections/{collection_id}/curation-rules")
async def get_curation_rules(collection_id: int, user_id: str = Query("default", description="User ID")):
    """Get all curation rules for a collection"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Verify collection exists and belongs to user
        cursor.execute("SELECT id FROM collections WHERE id = ? AND user_id = ?", (collection_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Collection not found")
        
        cursor.execute("""
            SELECT id, rule_name, rule_type, rule_config, is_active, created_at, updated_at
            FROM curation_rules 
            WHERE collection_id = ? AND user_id = ?
            ORDER BY is_active DESC, created_at DESC
        """, (collection_id, user_id))
        
        rules = []
        for row in cursor.fetchall():
            rules.append({
                "id": row["id"],
                "rule_name": row["rule_name"],
                "rule_type": row["rule_type"],
                "rule_config": json.loads(row["rule_config"]),
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            })
        
        conn.close()
        return {"rules": rules, "count": len(rules)}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/collections/{collection_id}/analysis")
async def analyze_collection(collection_id: int, user_id: str = Query("default", description="User ID")):
    """Get comprehensive analysis of a collection including tiles, layouts, and curation"""
    try:
        conn = collections_service._connect_db()
        cursor = conn.cursor()
        
        # Get collection data
        cursor.execute("""
            SELECT id, name, description, layout_config, created_at, updated_at
            FROM collections 
            WHERE id = ? AND user_id = ?
        """, (collection_id, user_id))
        
        collection_row = cursor.fetchone()
        if not collection_row:
            raise HTTPException(status_code=404, detail="Collection not found")
        
        collection = {
            "id": collection_row["id"],
            "name": collection_row["name"],
            "description": collection_row["description"],
            "layout_config": json.loads(collection_row["layout_config"]) if collection_row["layout_config"] else {},
            "created_at": collection_row["created_at"],
            "updated_at": collection_row["updated_at"]
        }
        
        # Get tiles
        cursor.execute("""
            SELECT tile_type, COUNT(*) as count, AVG(width) as avg_width, AVG(height) as avg_height
            FROM tiles 
            WHERE collection_id = ?
            GROUP BY tile_type
        """, (collection_id,))
        
        tile_stats = {}
        total_tiles = 0
        for row in cursor.fetchall():
            tile_stats[row["tile_type"]] = {
                "count": row["count"],
                "avg_width": row["avg_width"],
                "avg_height": row["avg_height"]
            }
            total_tiles += row["count"]
        
        # Get layouts
        cursor.execute("""
            SELECT COUNT(*) as layout_count, SUM(CASE WHEN is_default THEN 1 ELSE 0 END) as default_count
            FROM collection_layouts 
            WHERE collection_id = ? AND user_id = ?
        """, (collection_id, user_id))
        
        layout_stats = cursor.fetchone()
        
        # Get curation rules
        cursor.execute("""
            SELECT COUNT(*) as total_rules, SUM(CASE WHEN is_active THEN 1 ELSE 0 END) as active_rules
            FROM curation_rules 
            WHERE collection_id = ? AND user_id = ?
        """, (collection_id, user_id))
        
        curation_stats = cursor.fetchone()
        
        conn.close()
        
        # Generate analysis
        analysis = {
            "collection": collection,
            "tiles": {
                "total_count": total_tiles,
                "by_type": tile_stats,
                "coverage": "high" if total_tiles > 10 else "medium" if total_tiles > 5 else "low"
            },
            "layouts": {
                "total_count": layout_stats["layout_count"] if layout_stats else 0,
                "default_count": layout_stats["default_count"] if layout_stats else 0
            },
            "curation": {
                "total_rules": curation_stats["total_rules"] if curation_stats else 0,
                "active_rules": curation_stats["active_rules"] if curation_stats else 0
            },
            "insights": {
                "most_common_tile_type": max(tile_stats.keys(), key=lambda k: tile_stats[k]["count"]) if tile_stats else None,
                "layout_diversity": "high" if (layout_stats["layout_count"] if layout_stats and layout_stats["layout_count"] else 0) > 3 else "medium",
                "curation_level": "high" if (curation_stats["active_rules"] if curation_stats and curation_stats["active_rules"] else 0) > 2 else "medium" if (curation_stats["active_rules"] if curation_stats and curation_stats["active_rules"] else 0) > 0 else "low"
            }
        }
        
        return {"analysis": analysis}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
