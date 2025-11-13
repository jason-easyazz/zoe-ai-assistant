"""
People Management Router
Dedicated API for managing people relationships, profiles, and interactions
Separated from memories/collections system for clarity and future P2P features
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date
import sqlite3
import json
import os
import sys
sys.path.append('/app')
from auth_integration import validate_session, AuthenticatedSession

router = APIRouter(prefix="/api/people", tags=["people"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def init_people_db():
    """Initialize people tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # People table - now includes "self" entries for unified storage
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            name TEXT NOT NULL,
            relationship TEXT,
            is_self BOOLEAN DEFAULT 0,
            birthday DATE,
            phone TEXT,
            email TEXT,
            address TEXT,
            notes TEXT,
            profile TEXT,
            facts JSON,
            preferences JSON,
            important_dates JSON,
            personality_traits JSON,
            interests JSON,
            avatar_url TEXT,
            tags TEXT,
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Migration: Add new columns to existing tables
    migration_columns = [
        ("profile", "TEXT"),
        ("is_self", "BOOLEAN DEFAULT 0"),
        ("facts", "JSON"),
        ("preferences", "JSON"),
        ("important_dates", "JSON"),
        ("personality_traits", "JSON"),
        ("interests", "JSON"),
        ("folder_path", "TEXT")
    ]
    
    for col_name, col_type in migration_columns:
        try:
            cursor.execute(f"ALTER TABLE people ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    
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
            FOREIGN KEY (person_id) REFERENCES people(id) ON DELETE CASCADE
        )
    """)
    
    # Add missing columns to existing people table (migration)
    missing_columns = [
        ("relationship", "TEXT"),
        ("birthday", "DATE"),
        ("phone", "TEXT"),
        ("email", "TEXT"),
        ("address", "TEXT"),
        ("avatar_url", "TEXT")
    ]
    
    for col_name, col_type in missing_columns:
        try:
            cursor.execute(f"ALTER TABLE people ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_user ON people(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_is_self ON people(user_id, is_self)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_user ON relationships(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_timeline_person ON person_timeline(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_activities_person ON person_activities(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_person ON person_conversations(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gifts_person ON person_gifts(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dates_person ON person_important_dates(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_goals_person ON person_shared_goals(person_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_goals_status ON person_shared_goals(status, user_id)")
    
    conn.commit()
    conn.close()

# Initialize on import
init_people_db()

# Request/Response models
class PersonCreate(BaseModel):
    name: str
    relationship: Optional[str] = None
    is_self: Optional[bool] = False
    birthday: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    avatar_url: Optional[str] = None
    tags: Optional[List[str]] = None
    facts: Optional[Dict[str, Any]] = None
    preferences: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

class RelationshipCreate(BaseModel):
    person1_id: int
    person2_id: int
    relationship_type: str
    strength: Optional[float] = 0.5
    metadata: Optional[Dict[str, Any]] = None

# Core People Endpoints

@router.get("/self")
async def get_self(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get the authenticated user's own profile (is_self=true entry)"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM people 
        WHERE user_id = ? AND is_self = 1
        LIMIT 1
    """, (user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return {"self": None, "message": "No self entry found. Create one with POST /api/people"}
    
    # Build person dict with JSON parsing
    person = dict(row)
    for field in ['tags', 'facts', 'preferences', 'metadata', 'personality_traits', 'interests', 'important_dates']:
        if field in person and person[field]:
            try:
                person[field] = json.loads(person[field])
            except:
                person[field] = {} if field != 'tags' else []
    
    return {"self": person}

@router.patch("/self")
async def update_self(
    updates: Dict[str, Any],
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update the authenticated user's own profile"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get existing self entry
    cursor.execute("SELECT id FROM people WHERE user_id = ? AND is_self = 1", (user_id,))
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        raise HTTPException(status_code=404, detail="No self entry found. Create one with POST /api/people")
    
    person_id = result[0]
    
    # Build update query dynamically
    allowed_fields = ['name', 'birthday', 'phone', 'email', 'address', 'notes', 'avatar_url', 
                      'facts', 'preferences', 'personality_traits', 'interests', 'tags', 'metadata']
    
    update_parts = []
    values = []
    for key, value in updates.items():
        if key in allowed_fields:
            update_parts.append(f"{key} = ?")
            # Serialize JSON fields
            if key in ['facts', 'preferences', 'personality_traits', 'interests', 'tags', 'metadata']:
                values.append(json.dumps(value) if value else None)
            else:
                values.append(value)
    
    if not update_parts:
        conn.close()
        raise HTTPException(status_code=400, detail="No valid fields to update")
    
    update_parts.append("updated_at = CURRENT_TIMESTAMP")
    values.append(person_id)
    
    cursor.execute(f"""
        UPDATE people 
        SET {', '.join(update_parts)}
        WHERE id = ?
    """, tuple(values))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "person_id": person_id, "updated_fields": list(updates.keys())}

@router.get("")
async def get_people(
    limit: int = Query(100, description="Maximum number of people to return"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get all people for the authenticated user"""
    user_id = session.user_id
    
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
    """, (user_id, limit))
    
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

@router.post("")
async def create_person(
    person: PersonCreate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Create a new person for the authenticated user"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if trying to create duplicate "self" entry
    if person.is_self:
        cursor.execute("SELECT id FROM people WHERE user_id = ? AND is_self = 1", (user_id,))
        existing_self = cursor.fetchone()
        if existing_self:
            conn.close()
            raise HTTPException(status_code=400, detail="Self entry already exists. Use update endpoint instead.")
    
    cursor.execute("""
        INSERT INTO people (user_id, name, relationship, is_self, birthday, phone, email, 
                          address, notes, avatar_url, tags, facts, preferences, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, person.name, person.relationship, 1 if person.is_self else 0, 
        person.birthday, person.phone, person.email, person.address, person.notes, 
        person.avatar_url,
        json.dumps(person.tags) if person.tags else None,
        json.dumps(person.facts) if person.facts else None,
        json.dumps(person.preferences) if person.preferences else None,
        json.dumps(person.metadata) if person.metadata else None
    ))
    
    person_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"person": {"id": person_id, "name": person.name, "relationship": person.relationship, "is_self": person.is_self}}

@router.get("/{person_id}")
async def get_person(
    person_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get a specific person by ID for the authenticated user"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, name, relationship, birthday, phone, email, address, notes,
               avatar_url, tags, metadata, created_at, updated_at
        FROM people 
        WHERE id = ? AND user_id = ?
    """, (person_id, user_id))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")
    
    return {
        "person": {
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
        }
    }

@router.put("/{person_id}")
async def update_person(
    person_id: int,
    person: PersonCreate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Update a person for the authenticated user"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if person exists
    cursor.execute("SELECT id FROM people WHERE id = ? AND user_id = ?", (person_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Person not found")
    
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
        person_id, user_id
    ))
    
    conn.commit()
    conn.close()
    
    return {"message": "Person updated successfully"}

@router.delete("/{person_id}")
async def delete_person(
    person_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete a person for the authenticated user"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM people WHERE id = ? AND user_id = ?", (person_id, user_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Person not found")
    
    conn.commit()
    conn.close()
    
    return {"message": "Person deleted successfully"}

# Enhanced Person Data Endpoint

@router.get("/{person_id}/analysis")
async def get_person_analysis(
    person_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get enhanced person data including timeline, activities, conversations, gifts, etc. for the authenticated user"""
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

# Relationships Endpoints

@router.get("/{person_id}/relationships")
async def get_person_relationships(
    person_id: int,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get all relationships for a person for the authenticated user"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT r.id, r.person2_id, p.name, r.relationship_type, r.strength, r.metadata
        FROM relationships r
        JOIN people p ON r.person2_id = p.id
        WHERE r.person1_id = ? AND r.user_id = ?
    """, (person_id, user_id))
    
    relationships = []
    for row in cursor.fetchall():
        relationships.append({
            "id": row[0],
            "person_id": row[1],
            "person_name": row[2],
            "relationship_type": row[3],
            "strength": row[4],
            "metadata": json.loads(row[5]) if row[5] else None
        })
    
    conn.close()
    
    return {"relationships": relationships}

@router.post("/{person_id}/relationships")
async def create_relationship(
    person_id: int,
    relationship: RelationshipCreate,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Create a relationship between two people for the authenticated user"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO relationships (user_id, person1_id, person2_id, relationship_type, strength, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user_id, relationship.person1_id, relationship.person2_id,
        relationship.relationship_type, relationship.strength,
        json.dumps(relationship.metadata) if relationship.metadata else None
    ))
    
    relationship_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"relationship": {"id": relationship_id}}

# Search and Insights

@router.get("/search")
async def search_people(
    query: str = Query(..., description="Search query"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Search people by name, relationship, notes, etc. for the authenticated user"""
    user_id = session.user_id
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    search_pattern = f"%{query}%"
    cursor.execute("""
        SELECT id, name, relationship, birthday, phone, email, notes, metadata
        FROM people 
        WHERE user_id = ? AND (
            name LIKE ? OR
            relationship LIKE ? OR
            notes LIKE ? OR
            email LIKE ? OR
            phone LIKE ?
        )
        ORDER BY name ASC
        LIMIT 50
    """, (user_id, search_pattern, search_pattern, search_pattern, search_pattern, search_pattern))
    
    people = []
    for row in cursor.fetchall():
        people.append({
            "id": row[0],
            "name": row[1],
            "relationship": row[2],
            "birthday": row[3],
            "phone": row[4],
            "email": row[5],
            "notes": row[6],
            "metadata": json.loads(row[7]) if row[7] else None
        })
    
    conn.close()
    
    return {"people": people, "count": len(people)}

@router.post("/actions/execute")
async def execute_person_action(
    action: Dict[str, Any],
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Execute a person-related action via natural language interface
    Used by the Person Expert for chat-based interactions
    
    Action format:
    {
        "action_type": "add_person|add_note|add_gift|log_conversation|etc",
        "data": {...}
    }
    """
    user_id = session.user_id
    action_type = action.get("action_type")
    data = action.get("data", {})
    
    try:
        if action_type == "add_person":
            # Add person
            name = data.get("name")
            relationship = data.get("relationship")
            notes = data.get("notes")
            birthday = data.get("birthday")
            phone = data.get("phone")
            email = data.get("email")
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO people (user_id, name, relationship, birthday, phone, email, notes, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, name, relationship, birthday, phone, email, notes,
                json.dumps({"added_via": "chat", "added_at": datetime.now().isoformat()})
            ))
            
            person_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "message": f"Added {name} to your people!",
                "person_id": person_id
            }
        
        elif action_type == "add_note":
            # Add note to person
            person_id = data.get("person_id")
            note_text = data.get("note")
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("SELECT notes FROM people WHERE id = ? AND user_id = ?", (person_id, user_id))
            result = cursor.fetchone()
            
            if not result:
                conn.close()
                return {"success": False, "message": "Person not found"}
            
            existing_notes = result[0]
            updated_notes = f"{existing_notes}\n\n{datetime.now().strftime('%Y-%m-%d')}: {note_text}" if existing_notes else note_text
            
            cursor.execute("""
                UPDATE people SET notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
            """, (updated_notes, person_id, user_id))
            
            conn.commit()
            conn.close()
            
            return {"success": True, "message": "Note added!"}
        
        elif action_type == "add_gift":
            # Add gift idea
            person_id = data.get("person_id")
            gift_item = data.get("gift_item")
            occasion = data.get("occasion", "unspecified")
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO person_gifts (user_id, person_id, item, occasion, status)
                VALUES (?, ?, ?, ?, 'idea')
            """, (user_id, person_id, gift_item, occasion))
            
            conn.commit()
            conn.close()
            
            return {"success": True, "message": "Gift idea added!"}
        
        elif action_type == "log_conversation":
            # Log conversation
            person_id = data.get("person_id")
            topic = data.get("topic", "General")
            notes = data.get("notes", "")
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO person_conversations (user_id, person_id, topic, notes, conversation_date)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, person_id, topic, notes, datetime.now().strftime("%Y-%m-%d")))
            
            # Update last contact
            cursor.execute("""
                UPDATE people 
                SET metadata = json_set(COALESCE(metadata, '{}'), '$.lastContact', ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
            """, (datetime.now().isoformat(), person_id, user_id))
            
            conn.commit()
            conn.close()
            
            return {"success": True, "message": "Conversation logged!"}
        
        elif action_type == "search":
            # Search people
            query = data.get("query", "")
            search_pattern = f"%{query}%"
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, name, relationship, birthday, phone, email, notes
                FROM people
                WHERE user_id = ? AND (
                    name LIKE ? OR relationship LIKE ? OR notes LIKE ?
                )
                LIMIT 10
            """, (user_id, search_pattern, search_pattern, search_pattern))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "name": row[1],
                    "relationship": row[2],
                    "birthday": row[3],
                    "phone": row[4],
                    "email": row[5],
                    "notes": row[6]
                })
            
            conn.close()
            
            return {
                "success": True,
                "results": results,
                "count": len(results)
            }
        
        else:
            return {
                "success": False,
                "message": f"Unknown action type: {action_type}"
            }
    
    except Exception as e:
        logger.error(f"Error executing person action: {e}")
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }

