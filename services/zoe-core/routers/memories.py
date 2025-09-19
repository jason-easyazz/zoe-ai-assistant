"""
Unified Memory Management System
Handles people, projects, notes, relationships, and memory facts
Combines basic CRUD with advanced memory features
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date
import sqlite3
import json
import os
import sys
sys.path.append('/app')
from memory_system import MemorySystem

router = APIRouter(prefix="/api/memories", tags=["memories"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
MEMORY_DB_PATH = "/app/data/memory.db"

# Initialize advanced memory system
memory_system = MemorySystem(MEMORY_DB_PATH)

def init_memories_db():
    """Initialize memories tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # People table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            name TEXT NOT NULL,
            relationship TEXT,
            birthday DATE,
            phone TEXT,
            email TEXT,
            address TEXT,
            notes TEXT,
            avatar_url TEXT,
            tags TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Projects table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            name TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'active',
            start_date DATE,
            end_date DATE,
            priority TEXT DEFAULT 'medium',
            tags TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Notes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            title TEXT NOT NULL,
            content TEXT,
            category TEXT,
            tags TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_user ON people(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id)")
    
    conn.commit()
    conn.close()

# Initialize on import
init_memories_db()

# Request/Response models
class PersonCreate(BaseModel):
    name: str
    relationship: Optional[str] = None
    birthday: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    avatar_url: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    status: Optional[str] = "active"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    priority: Optional[str] = "medium"
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

class NoteCreate(BaseModel):
    title: str
    content: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

@router.get("/")
async def get_memories(
    type: str = Query(..., description="Type: people, projects, or notes"),
    user_id: str = Query("default", description="User ID")
):
    """Get memories by type"""
    if type not in ["people", "projects", "notes"]:
        raise HTTPException(status_code=400, detail="Type must be people, projects, or notes")
    
    conn = sqlite3.connect(DB_PATH)
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

@router.post("/")
async def create_memory(
    type: str = Query(..., description="Type: people, projects, or notes"),
    user_id: str = Query("default", description="User ID"),
    person: Optional[PersonCreate] = None,
    project: Optional[ProjectCreate] = None,
    note: Optional[NoteCreate] = None
):
    """Create a new memory"""
    if type not in ["people", "projects", "notes"]:
        raise HTTPException(status_code=400, detail="Type must be people, projects, or notes")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if type == "people" and person:
        cursor.execute("""
            INSERT INTO people (user_id, name, relationship, birthday, phone, email, 
                              address, notes, avatar_url, tags, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, person.name, person.relationship, person.birthday, person.phone,
            person.email, person.address, person.notes, person.avatar_url,
            json.dumps(person.tags) if person.tags else None,
            json.dumps(person.metadata) if person.metadata else None
        ))
        
    elif type == "projects" and project:
        cursor.execute("""
            INSERT INTO projects (user_id, name, description, status, start_date, 
                                end_date, priority, tags, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, project.name, project.description, project.status, project.start_date,
            project.end_date, project.priority,
            json.dumps(project.tags) if project.tags else None,
            json.dumps(project.metadata) if project.metadata else None
        ))
        
    elif type == "notes" and note:
        cursor.execute("""
            INSERT INTO notes (user_id, title, content, category, tags, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id, note.title, note.content, note.category,
            json.dumps(note.tags) if note.tags else None,
            json.dumps(note.metadata) if note.metadata else None
        ))
    
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid type or missing data")
    
    memory_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"memory": {"id": memory_id, "type": type}}

@router.get("/item/{memory_id}")
async def get_memory(
    memory_id: int,
    type: str = Query(..., description="Type: people, projects, or notes"),
    user_id: str = Query("default", description="User ID")
):
    """Get a specific memory"""
    if type not in ["people", "projects", "notes"]:
        raise HTTPException(status_code=400, detail="Type must be people, projects, or notes")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    table_name = type
    cursor.execute(f"""
        SELECT * FROM {table_name} 
        WHERE id = ? AND user_id = ?
    """, (memory_id, user_id))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Memory not found")
    
    # Return appropriate fields based on type
    if type == "people":
        return {
            "id": row[0],
            "name": row[2],
            "relationship": row[3],
            "birthday": row[4],
            "phone": row[5],
            "email": row[6],
            "address": row[7],
            "notes": row[8],
            "avatar_url": row[9],
            "tags": json.loads(row[10]) if row[10] else None,
            "metadata": json.loads(row[11]) if row[11] else None,
            "created_at": row[12],
            "updated_at": row[13]
        }
    elif type == "projects":
        return {
            "id": row[0],
            "name": row[2],
            "description": row[3],
            "status": row[4],
            "start_date": row[5],
            "end_date": row[6],
            "priority": row[7],
            "tags": json.loads(row[8]) if row[8] else None,
            "metadata": json.loads(row[9]) if row[9] else None,
            "created_at": row[10],
            "updated_at": row[11]
        }
    elif type == "notes":
        return {
            "id": row[0],
            "title": row[2],
            "content": row[3],
            "category": row[4],
            "tags": json.loads(row[5]) if row[5] else None,
            "metadata": json.loads(row[6]) if row[6] else None,
            "created_at": row[7],
            "updated_at": row[8]
        }

@router.put("/item/{memory_id}")
async def update_memory(
    memory_id: int,
    type: str = Query(..., description="Type: people, projects, or notes"),
    user_id: str = Query("default", description="User ID"),
    person: Optional[PersonCreate] = None,
    project: Optional[ProjectCreate] = None,
    note: Optional[NoteCreate] = None
):
    """Update a memory"""
    if type not in ["people", "projects", "notes"]:
        raise HTTPException(status_code=400, detail="Type must be people, projects, or notes")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if memory exists
    table_name = type
    cursor.execute(f"SELECT id FROM {table_name} WHERE id = ? AND user_id = ?", (memory_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Memory not found")
    
    # Update based on type
    if type == "people" and person:
        cursor.execute("""
            UPDATE people SET name = ?, relationship = ?, birthday = ?, phone = ?, 
                            email = ?, address = ?, notes = ?, avatar_url = ?, 
                            tags = ?, metadata = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
        """, (
            person.name, person.relationship, person.birthday, person.phone,
            person.email, person.address, person.notes, person.avatar_url,
            json.dumps(person.tags) if person.tags else None,
            json.dumps(person.metadata) if person.metadata else None,
            memory_id, user_id
        ))
        
    elif type == "projects" and project:
        cursor.execute("""
            UPDATE projects SET name = ?, description = ?, status = ?, start_date = ?, 
                              end_date = ?, priority = ?, tags = ?, metadata = ?, 
                              updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
        """, (
            project.name, project.description, project.status, project.start_date,
            project.end_date, project.priority,
            json.dumps(project.tags) if project.tags else None,
            json.dumps(project.metadata) if project.metadata else None,
            memory_id, user_id
        ))
        
    elif type == "notes" and note:
        cursor.execute("""
            UPDATE notes SET title = ?, content = ?, category = ?, tags = ?, 
                           metadata = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
        """, (
            note.title, note.content, note.category,
            json.dumps(note.tags) if note.tags else None,
            json.dumps(note.metadata) if note.metadata else None,
            memory_id, user_id
        ))
    
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid type or missing data")
    
    conn.commit()
    conn.close()
    
    return {"message": "Memory updated successfully"}

@router.delete("/item/{memory_id}")
async def delete_memory(
    memory_id: int,
    type: str = Query(..., description="Type: people, projects, or notes"),
    user_id: str = Query("default", description="User ID")
):
    """Delete a memory"""
    if type not in ["people", "projects", "notes"]:
        raise HTTPException(status_code=400, detail="Type must be people, projects, or notes")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    table_name = type
    cursor.execute(f"DELETE FROM {table_name} WHERE id = ? AND user_id = ?", (memory_id, user_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Memory not found")
    
    conn.commit()
    conn.close()
    
    return {"message": "Memory deleted successfully"}

# Advanced Memory System Endpoints

@router.post("/search")
async def search_memories(query: str = Query(..., description="Search query")):
    """Search across all memories using semantic search"""
    try:
        # Use basic text search for now (vector search can be added later)
        results = memory_system.search_memories(query)
        return {"results": results, "query": query, "search_type": "text"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/person/{name}/context")
async def get_person_context(name: str):
    """Get full context about a person including relationships and facts"""
    try:
        context = memory_system.get_person_context(name)
        if not context["found"]:
            raise HTTPException(status_code=404, detail="Person not found")
        return context
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/relationship")
async def add_relationship(
    person1: str = Query(..., description="First person"),
    person2: str = Query(..., description="Second person"),
    relationship: str = Query(..., description="Relationship type")
):
    """Add relationship between two people"""
    try:
        result = memory_system.add_relationship(person1, person2, relationship)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/facts")
async def add_memory_fact(
    entity_type: str = Query(..., description="Entity type: person, project, general"),
    entity_id: int = Query(..., description="Entity ID"),
    fact: str = Query(..., description="Fact to remember"),
    category: str = Query("general", description="Fact category"),
    importance: int = Query(5, description="Importance level 1-10")
):
    """Add a memory fact to an entity"""
    try:
        conn = sqlite3.connect(MEMORY_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO memory_facts (entity_type, entity_id, fact, category, importance)
            VALUES (?, ?, ?, ?, ?)
        """, (entity_type, entity_id, fact, category, importance))
        
        fact_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {"fact_id": fact_id, "message": "Memory fact added successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/facts")
async def get_memory_facts(
    entity_type: Optional[str] = Query(None, description="Filter by entity type"),
    entity_id: Optional[int] = Query(None, description="Filter by entity ID"),
    limit: int = Query(50, description="Number of facts to return")
):
    """Get memory facts with optional filtering"""
    try:
        conn = sqlite3.connect(MEMORY_DB_PATH)
        cursor = conn.cursor()
        
        query = "SELECT * FROM memory_facts WHERE 1=1"
        params = []
        
        if entity_type:
            query += " AND entity_type = ?"
            params.append(entity_type)
        
        if entity_id:
            query += " AND entity_id = ?"
            params.append(entity_id)
        
        query += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        facts = []
        
        for row in cursor.fetchall():
            facts.append({
                "id": row[0],
                "entity_type": row[1],
                "entity_id": row[2],
                "fact": row[3],
                "category": row[4],
                "importance": row[5],
                "source": row[6],
                "created_at": row[7]
            })
        
        conn.close()
        return {"facts": facts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
