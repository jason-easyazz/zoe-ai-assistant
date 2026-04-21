"""
Unified Memory Management System
Handles people, projects, notes, relationships, and memory facts
Combines basic CRUD with advanced memory features
"""
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date
import sqlite3
import json
import os
import sys
import httpx
sys.path.append('/app')
from memory_system import MemorySystem
from light_rag_memory import LightRAGMemorySystem
from auth_integration import validate_session, AuthenticatedSession

router = APIRouter(prefix="/api/memories", tags=["memories"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
MEMORY_DB_PATH = os.getenv("MEMORY_DB_PATH", "/app/data/memory.db")

# Initialize advanced memory system
memory_system = MemorySystem(MEMORY_DB_PATH)

# Initialize Light RAG memory system
light_rag_system = LightRAGMemorySystem(MEMORY_DB_PATH)

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
            profile TEXT,
            avatar_url TEXT,
            tags TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Add profile column to existing tables (migration)
    try:
        cursor.execute("ALTER TABLE people ADD COLUMN profile TEXT")
    except sqlite3.OperationalError:
        # Column already exists
        pass
    
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
    
    # Collections table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            name TEXT NOT NULL,
            icon TEXT DEFAULT '📁',
            color TEXT DEFAULT '#3b82f6',
            x REAL DEFAULT 0,
            y REAL DEFAULT 0,
            size INTEGER DEFAULT 60,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Tiles table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            collection_id INTEGER,
            name TEXT,
            content TEXT,
            preview_type TEXT,
            preview_data JSON,
            x REAL DEFAULT 0,
            y REAL DEFAULT 0,
            size INTEGER DEFAULT 50,
            tile_x REAL,
            tile_y REAL,
            tile_width INTEGER,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
        )
    """)
    
    # Relationships table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person1_id INTEGER,
            person2_id INTEGER,
            relationship_type TEXT,
            strength REAL DEFAULT 0.5,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (person1_id) REFERENCES people(id) ON DELETE CASCADE,
            FOREIGN KEY (person2_id) REFERENCES people(id) ON DELETE CASCADE
        )
    """)
    
    # Enhanced person data tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS person_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER,
            event_type TEXT,
            event_text TEXT,
            event_date TEXT,
            location TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS person_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER,
            activity TEXT,
            frequency TEXT,
            last_done TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS person_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER,
            topic TEXT,
            notes TEXT,
            conversation_date TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS person_gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER,
            item TEXT,
            occasion TEXT,
            status TEXT DEFAULT 'idea',
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS person_important_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER,
            name TEXT,
            date TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
        )
    """)
    
    # Shared goals table for person-based goal tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS person_shared_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            person_id INTEGER NOT NULL,
            goal_text TEXT NOT NULL,
            goal_type TEXT DEFAULT 'general',
            status TEXT DEFAULT 'active',
            target_date DATE,
            journey_id INTEGER,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE,
            FOREIGN KEY (journey_id) REFERENCES journeys(id)
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_user ON people(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_collections_user ON collections(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tiles_collection ON tiles(collection_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_user ON relationships(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_timeline_person ON person_timeline(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_activities_person ON person_activities(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_person ON person_conversations(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gifts_person ON person_gifts(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dates_person ON person_important_dates(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_goals_person ON person_shared_goals(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_goals_status ON person_shared_goals(status, user_id)")
    
    # Add columns to person_activities for catch-up tracking
    try:
        cursor.execute("ALTER TABLE person_activities ADD COLUMN last_prompted_journal TIMESTAMP")
    except:
        pass
    
    try:
        cursor.execute("ALTER TABLE person_activities ADD COLUMN calendar_event_id INTEGER")
    except:
        pass
    
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

class CollectionCreate(BaseModel):
    name: str
    icon: Optional[str] = "📁"
    color: Optional[str] = "#3b82f6"
    x: Optional[float] = 0
    y: Optional[float] = 0
    size: Optional[int] = 60
    metadata: Optional[Dict[str, Any]] = None

class TileCreate(BaseModel):
    collection_id: int
    name: Optional[str] = None
    content: Optional[str] = None
    preview_type: Optional[str] = None
    preview_data: Optional[Dict[str, Any]] = None
    x: Optional[float] = 0
    y: Optional[float] = 0
    size: Optional[int] = 50
    tile_x: Optional[float] = None
    tile_y: Optional[float] = None
    tile_width: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None

class RelationshipCreate(BaseModel):
    person1_id: int
    person2_id: int
    relationship_type: str
    strength: Optional[float] = 0.5
    metadata: Optional[Dict[str, Any]] = None

class TimelineEventCreate(BaseModel):
    person_id: int
    event_type: str
    event_text: str
    event_date: str
    location: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class ActivityCreate(BaseModel):
    person_id: int
    activity: str
    frequency: str
    last_done: str
    metadata: Optional[Dict[str, Any]] = None

class ConversationCreate(BaseModel):
    person_id: int
    topic: str
    notes: str
    conversation_date: str
    metadata: Optional[Dict[str, Any]] = None

class GiftCreate(BaseModel):
    person_id: int
    item: str
    occasion: str
    status: Optional[str] = "idea"
    metadata: Optional[Dict[str, Any]] = None

class ImportantDateCreate(BaseModel):
    person_id: int
    name: str
    date: str
    metadata: Optional[Dict[str, Any]] = None

@router.get("/")
async def get_memories(
    type: str = Query(..., description="Type: people, projects, or notes"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get memories by type"""
    user_id = session.user_id
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
    person: Optional[PersonCreate] = None,
    project: Optional[ProjectCreate] = None,
    note: Optional[NoteCreate] = None,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Create a new memory"""
    user_id = session.user_id
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
    session: AuthenticatedSession = Depends(validate_session),
):
    """Get a specific memory"""
    user_id = session.user_id
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
    person: Optional[PersonCreate] = None,
    project: Optional[ProjectCreate] = None,
    note: Optional[NoteCreate] = None,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Update a memory"""
    user_id = session.user_id
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
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete a memory"""
    user_id = session.user_id
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

@router.get("/search")
@router.post("/search")
async def search_memories(query: str = Query(..., description="Search query")):
    """Search across all memories using semantic search"""
    try:
        # Use basic text search for now (vector search can be added later)
        results = memory_system.search_memories(query)
        return {"results": results, "query": query, "search_type": "text"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Light RAG Enhanced Endpoints

@router.post("/search/light-rag")
async def light_rag_search(
    query: str = Query(..., description="Search query"),
    limit: int = Query(10, description="Number of results"),
    use_cache: bool = Query(True, description="Use search cache")
):
    """Enhanced search using Light RAG with vector embeddings and relationship awareness"""
    try:
        results = light_rag_system.light_rag_search(query, limit, use_cache)
        
        # Convert MemoryResult objects to dictionaries for JSON serialization
        results_dict = []
        for result in results:
            results_dict.append({
                "fact_id": result.fact_id,
                "fact": result.fact,
                "entity_type": result.entity_type,
                "entity_id": result.entity_id,
                "entity_name": result.entity_name,
                "category": result.category,
                "importance": result.importance,
                "similarity_score": result.similarity_score,
                "relationship_boost": result.relationship_boost,
                "final_score": result.final_score,
                "entity_context": result.entity_context,
                "relationship_path": result.relationship_path,
                "created_at": result.created_at
            })
        
        return {
            "results": results_dict,
            "query": query,
            "search_type": "light_rag",
            "total_results": len(results_dict),
            "limit": limit,
            "cache_used": use_cache
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/memories/enhanced")
async def add_enhanced_memory(
    entity_type: str = Query(..., description="Entity type: person, project, general"),
    entity_id: int = Query(..., description="Entity ID"),
    fact: str = Query(..., description="Fact to remember"),
    category: str = Query("general", description="Fact category"),
    importance: int = Query(5, description="Importance level 1-10"),
    source: str = Query("user", description="Source of the memory")
):
    """Add memory with automatic embedding generation and relationship context"""
    try:
        result = light_rag_system.add_memory_with_embedding(
            entity_type, entity_id, fact, category, importance, source
        )
        return {
            "success": True,
            "message": "Memory added with Light RAG enhancement",
            "data": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/contextual/{entity_name}")
async def get_contextual_memories(
    entity_name: str,
    context_type: str = Query("all", description="Context type: all, direct, related")
):
    """Get memories with full contextual awareness including relationships"""
    try:
        memories = light_rag_system.get_contextual_memories(entity_name, context_type)
        return {
            "entity": entity_name,
            "memories": memories,
            "context_type": context_type,
            "total_memories": len(memories)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/migrate")
async def migrate_memories_to_light_rag():
    """Migrate existing memories to include embeddings and relationship context"""
    try:
        result = light_rag_system.migrate_existing_memories()
        return {
            "success": True,
            "message": "Memory migration completed",
            "migration_stats": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats/light-rag")
async def get_light_rag_stats():
    """Get Light RAG system statistics and performance metrics"""
    try:
        stats = light_rag_system.get_system_stats()
        return {
            "system_stats": stats,
            "status": "operational"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/search/comparison")
async def compare_search_methods(
    query: str = Query(..., description="Search query to compare"),
    limit: int = Query(10, description="Number of results")
):
    """Compare traditional search vs Light RAG search for the same query"""
    try:
        # Traditional search
        traditional_results = memory_system.search_memories(query)
        
        # Light RAG search
        light_rag_results = light_rag_system.light_rag_search(query, limit)
        
        # Convert Light RAG results to comparable format
        light_rag_formatted = []
        for result in light_rag_results:
            light_rag_formatted.append({
                "fact": result.fact,
                "type": result.entity_type,
                "entity": result.entity_name,
                "importance": result.importance,
                "date": result.created_at,
                "similarity_score": result.similarity_score,
                "final_score": result.final_score
            })
        
        return {
            "query": query,
            "traditional_search": {
                "results": traditional_results,
                "count": len(traditional_results),
                "method": "text_matching"
            },
            "light_rag_search": {
                "results": light_rag_formatted,
                "count": len(light_rag_formatted),
                "method": "vector_embeddings_with_relationships"
            },
            "comparison": {
                "traditional_count": len(traditional_results),
                "light_rag_count": len(light_rag_formatted),
                "improvement_factor": len(light_rag_formatted) / max(len(traditional_results), 1)
            }
        }
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

# Collections endpoints
@router.get("/collections")
async def get_collections(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get all collections for a user"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, name, icon, color, x, y, size, metadata, created_at, updated_at
        FROM collections 
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,))
    
    collections = []
    for row in cursor.fetchall():
        collections.append({
            "id": row[0],
            "name": row[1],
            "icon": row[2],
            "color": row[3],
            "x": row[4],
            "y": row[5],
            "size": row[6],
            "metadata": json.loads(row[7]) if row[7] else None,
            "created_at": row[8],
            "updated_at": row[9]
        })
    
    conn.close()
    return {"collections": collections}

@router.post("/collections")
async def create_collection(
    collection: CollectionCreate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Create a new collection"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO collections (user_id, name, icon, color, x, y, size, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, collection.name, collection.icon, collection.color,
        collection.x, collection.y, collection.size,
        json.dumps(collection.metadata) if collection.metadata else None
    ))
    
    collection_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"collection": {"id": collection_id, "name": collection.name}}

# Tiles endpoints
@router.get("/collections/{collection_id}/tiles")
async def get_tiles(
    collection_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get all tiles in a collection"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, name, content, preview_type, preview_data, x, y, size,
               tile_x, tile_y, tile_width, metadata, created_at, updated_at
        FROM tiles 
        WHERE collection_id = ? AND user_id = ?
        ORDER BY created_at DESC
    """, (collection_id, user_id))
    
    tiles = []
    for row in cursor.fetchall():
        tiles.append({
            "id": row[0],
            "name": row[1],
            "content": row[2],
            "preview_type": row[3],
            "preview_data": json.loads(row[4]) if row[4] else None,
            "x": row[5],
            "y": row[6],
            "size": row[7],
            "tile_x": row[8],
            "tile_y": row[9],
            "tile_width": row[10],
            "metadata": json.loads(row[11]) if row[11] else None,
            "created_at": row[12],
            "updated_at": row[13]
        })
    
    conn.close()
    return {"tiles": tiles}

@router.post("/tiles")
async def create_tile(
    tile: TileCreate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Create a new tile"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO tiles (user_id, collection_id, name, content, preview_type, preview_data,
                          x, y, size, tile_x, tile_y, tile_width, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, tile.collection_id, tile.name, tile.content, tile.preview_type,
        json.dumps(tile.preview_data) if tile.preview_data else None,
        tile.x, tile.y, tile.size, tile.tile_x, tile.tile_y, tile.tile_width,
        json.dumps(tile.metadata) if tile.metadata else None
    ))
    
    tile_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"tile": {"id": tile_id, "name": tile.name}}

@router.put("/tiles/{tile_id}")
async def update_tile(
    tile_id: int,
    tile: TileCreate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update a tile"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE tiles SET name = ?, content = ?, preview_type = ?, preview_data = ?,
                        x = ?, y = ?, size = ?, tile_x = ?, tile_y = ?, tile_width = ?,
                        metadata = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
    """, (
        tile.name, tile.content, tile.preview_type,
        json.dumps(tile.preview_data) if tile.preview_data else None,
        tile.x, tile.y, tile.size, tile.tile_x, tile.tile_y, tile.tile_width,
        json.dumps(tile.metadata) if tile.metadata else None,
        tile_id, user_id
    ))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Tile not found")
    
    conn.commit()
    conn.close()
    
    return {"message": "Tile updated successfully"}

@router.delete("/tiles/{tile_id}")
async def delete_tile(
    tile_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete a tile"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM tiles WHERE id = ? AND user_id = ?", (tile_id, user_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Tile not found")
    
    conn.commit()
    conn.close()
    
    return {"message": "Tile deleted successfully"}

# Enhanced person data endpoints
@router.get("/people/{person_id}/enhanced")
async def get_enhanced_person(
    person_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get enhanced person data including timeline, activities, etc."""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get basic person data
    cursor.execute("""
        SELECT id, name, relationship, birthday, phone, email, address, notes,
               avatar_url, tags, metadata, created_at, updated_at
        FROM people 
        WHERE id = ? AND user_id = ?
    """, (person_id, user_id))
    
    person_row = cursor.fetchone()
    if not person_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Person not found")
    
    person_data = {
        "id": person_row[0],
        "name": person_row[1],
        "relationship": person_row[2],
        "birthday": person_row[3],
        "phone": person_row[4],
        "email": person_row[5],
        "address": person_row[6],
        "notes": person_row[7],
        "avatar_url": person_row[8],
        "tags": json.loads(person_row[9]) if person_row[9] else None,
        "metadata": json.loads(person_row[10]) if person_row[10] else None,
        "created_at": person_row[11],
        "updated_at": person_row[12]
    }
    
    # Get timeline events
    cursor.execute("""
        SELECT event_type, event_text, event_date, location, metadata
        FROM person_timeline 
        WHERE person_id = ? AND user_id = ?
        ORDER BY event_date DESC
    """, (person_id, user_id))
    
    timeline = []
    for row in cursor.fetchall():
        timeline.append({
            "type": row[0],
            "text": row[1],
            "date": row[2],
            "location": row[3],
            "metadata": json.loads(row[4]) if row[4] else None
        })
    
    # Get activities
    cursor.execute("""
        SELECT activity, frequency, last_done, metadata
        FROM person_activities 
        WHERE person_id = ? AND user_id = ?
        ORDER BY created_at DESC
    """, (person_id, user_id))
    
    activities = []
    for row in cursor.fetchall():
        activities.append({
            "activity": row[0],
            "frequency": row[1],
            "last_done": row[2],
            "metadata": json.loads(row[3]) if row[3] else None
        })
    
    # Get conversations
    cursor.execute("""
        SELECT topic, notes, conversation_date, metadata
        FROM person_conversations 
        WHERE person_id = ? AND user_id = ?
        ORDER BY conversation_date DESC
    """, (person_id, user_id))
    
    conversations = []
    for row in cursor.fetchall():
        conversations.append({
            "topic": row[0],
            "notes": row[1],
            "date": row[2],
            "metadata": json.loads(row[3]) if row[3] else None
        })
    
    # Get gifts
    cursor.execute("""
        SELECT item, occasion, status, metadata
        FROM person_gifts 
        WHERE person_id = ? AND user_id = ?
        ORDER BY created_at DESC
    """, (person_id, user_id))
    
    gifts = []
    for row in cursor.fetchall():
        gifts.append({
            "item": row[0],
            "occasion": row[1],
            "status": row[2],
            "metadata": json.loads(row[3]) if row[3] else None
        })
    
    # Get important dates
    cursor.execute("""
        SELECT name, date, metadata
        FROM person_important_dates 
        WHERE person_id = ? AND user_id = ?
        ORDER BY date ASC
    """, (person_id, user_id))
    
    important_dates = []
    for row in cursor.fetchall():
        important_dates.append({
            "name": row[0],
            "date": row[1],
            "metadata": json.loads(row[2]) if row[2] else None
        })
    
    # Get relationships
    cursor.execute("""
        SELECT r.person2_id, p.name, r.relationship_type, r.strength
        FROM relationships r
        JOIN people p ON r.person2_id = p.id
        WHERE r.person1_id = ? AND r.user_id = ?
    """, (person_id, user_id))
    
    relationships = []
    for row in cursor.fetchall():
        relationships.append({
            "person_id": row[0],
            "person_name": row[1],
            "relationship_type": row[2],
            "strength": row[3]
        })
    
    conn.close()
    
    person_data.update({
        "timeline": timeline,
        "activities": activities,
        "conversations": conversations,
        "gifts": gifts,
        "important_dates": important_dates,
        "relationships": relationships
    })
    
    return {"person": person_data}

# Dedicated people endpoint
@router.get("/people")
async def get_people(
    session: AuthenticatedSession = Depends(validate_session),
    limit: int = Query(100, description="Maximum number of people to return")
):
    """Get all people for the user"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get all available columns dynamically
    cursor.execute("PRAGMA table_info(people)")
    columns = [col[1] for col in cursor.fetchall()]
    
    # Build SELECT statement with available columns
    select_cols = ", ".join(columns)
    
    cursor.execute(f"""
        SELECT {select_cols}
        FROM people 
        WHERE user_id = ?
        ORDER BY name ASC
        LIMIT ?
    """, (session.user_id, limit))
    
    people = []
    for row in cursor.fetchall():
        person = {}
        for col in columns:
            value = row[col]
            # Parse JSON fields
            if col in ['tags', 'metadata'] and value:
                try:
                    person[col] = json.loads(value)
                except:
                    person[col] = [] if col == 'tags' else {}
            else:
                person[col] = value
        people.append(person)
    
    conn.close()
    
    return {"people": people, "count": len(people)}

@router.post("/people")
async def create_person(
    person: PersonCreate,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Create a new person"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO people (user_id, name, relationship, birthday, phone, email, 
                          address, notes, avatar_url, tags, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session.user_id, person.name, person.relationship, person.birthday, person.phone,
        person.email, person.address, person.notes, person.avatar_url,
        json.dumps(person.tags) if person.tags else None,
        json.dumps(person.metadata) if person.metadata else None
    ))
    
    person_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"person": {"id": person_id, "name": person.name, "relationship": person.relationship}}

# Link preview endpoint
@router.post("/link-preview")
async def get_link_preview(url: str = Query(..., description="URL to preview")):
    """Get link preview data"""
    try:
        import requests
        from urllib.parse import urlparse
        
        # Simple link preview - in production you'd use a proper service
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.replace('www.', '')
        
        return {
            "url": url,
            "title": f"{domain} Preview",
            "description": "This is a preview of the link. In production, this would fetch real metadata.",
            "image": "🔗",
            "domain": domain
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Proxy endpoints for microservices to avoid CORS issues
@router.get("/proxy/people")
async def proxy_people_endpoint(request: Request, session: AuthenticatedSession = Depends(validate_session)):
    """Proxy requests to people-service"""
    try:
        async with httpx.AsyncClient() as client:
            # Forward the request to people-service with session ID
            response = await client.get(
                "http://people-service-test:8001/people",
                headers={"X-Session-ID": session.session_id}
            )
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/proxy/collections")
async def proxy_collections_endpoint(request: Request, session: AuthenticatedSession = Depends(validate_session)):
    """Proxy requests to collections-service"""
    try:
        async with httpx.AsyncClient() as client:
            # Forward the request to collections-service with session ID
            response = await client.get(
                "http://collections-service-test:8005/collections",
                headers={"X-Session-ID": session.session_id}
            )
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
