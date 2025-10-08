#!/usr/bin/env python3
"""
Zoe People Service - Dedicated service for people management
Extracted from memories router with enhanced relationship analysis and timeline management
"""

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, date
import sqlite3
import json
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.append('/app')

app = FastAPI(title="Zoe People Service", version="1.0.0")

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
MEMORY_DB_PATH = "/app/data/memory.db"

# Initialize people service
class PeopleService:
    def __init__(self, db_path: str, memory_db_path: str):
        self.db_path = db_path
        self.memory_db_path = memory_db_path
        self.memory_dir = Path("/app/data/memory")
        self.init_database()
        self.init_folders()
    
    def init_database(self):
        """Initialize people database tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # People table (unified schema)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                name TEXT NOT NULL,
                folder_path TEXT,
                profile JSON DEFAULT '{}',
                facts JSON DEFAULT '{}',
                important_dates JSON DEFAULT '{}',
                preferences JSON DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name)
            )
        """)
        
        # Relationships table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                person1_id INTEGER NOT NULL,
                person2_id INTEGER NOT NULL,
                relationship_type TEXT NOT NULL,
                strength INTEGER DEFAULT 5,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (person1_id) REFERENCES people(id),
                FOREIGN KEY (person2_id) REFERENCES people(id)
            )
        """)
        
        # Timeline events
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS timeline_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                person_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_title TEXT NOT NULL,
                event_description TEXT,
                event_date DATE,
                importance INTEGER DEFAULT 5,
                metadata JSON DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (person_id) REFERENCES people(id)
            )
        """)
        
        conn.commit()
        conn.close()
    
    def init_folders(self):
        """Create folder structure for people data"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        (self.memory_dir / "people").mkdir(exist_ok=True)
    
    def _connect_db(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

# Initialize service
people_service = PeopleService(DB_PATH, MEMORY_DB_PATH)

# Pydantic models
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

class PersonUpdate(BaseModel):
    name: Optional[str] = None
    relationship: Optional[str] = None
    birthday: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    avatar_url: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

class RelationshipCreate(BaseModel):
    person1_id: int
    person2_id: int
    relationship_type: str
    strength: Optional[int] = 5
    notes: Optional[str] = None

class TimelineEventCreate(BaseModel):
    person_id: int
    event_type: str
    event_title: str
    event_description: Optional[str] = None
    event_date: Optional[str] = None
    importance: Optional[int] = 5
    metadata: Optional[Dict[str, Any]] = None

# API Endpoints
@app.get("/")
async def root():
    """Service health check"""
    return {"service": "Zoe People Service", "status": "healthy", "version": "1.0.0"}

@app.get("/people")
async def get_people(user_id: str = Query("default", description="User ID")):
    """Get all people for a user"""
    try:
        conn = people_service._connect_db()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, profile, facts, important_dates, preferences, 
                   created_at, updated_at
            FROM people 
            WHERE user_id = ?
            ORDER BY name
        """, (user_id,))
        
        people = []
        for row in cursor.fetchall():
            people.append({
                "id": row["id"],
                "name": row["name"],
                "profile": json.loads(row["profile"]) if row["profile"] else {},
                "facts": json.loads(row["facts"]) if row["facts"] else {},
                "important_dates": json.loads(row["important_dates"]) if row["important_dates"] else {},
                "preferences": json.loads(row["preferences"]) if row["preferences"] else {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            })
        
        conn.close()
        return {"people": people, "count": len(people)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/people/{person_id}")
async def get_person(person_id: int, user_id: str = Query("default", description="User ID")):
    """Get a specific person by ID"""
    try:
        conn = people_service._connect_db()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, profile, facts, important_dates, preferences,
                   created_at, updated_at
            FROM people 
            WHERE id = ? AND user_id = ?
        """, (person_id, user_id))
        
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")
        
        person = {
            "id": row["id"],
            "name": row["name"],
            "profile": json.loads(row["profile"]) if row["profile"] else {},
            "facts": json.loads(row["facts"]) if row["facts"] else {},
            "important_dates": json.loads(row["important_dates"]) if row["important_dates"] else {},
            "preferences": json.loads(row["preferences"]) if row["preferences"] else {},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }
        
        conn.close()
        return {"person": person}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/people")
async def create_person(person: PersonCreate, user_id: str = Query("default", description="User ID")):
    """Create a new person"""
    try:
        conn = people_service._connect_db()
        cursor = conn.cursor()
        
        # Create profile JSON
        profile = {
            "relationship": person.relationship,
            "birthday": person.birthday,
            "phone": person.phone,
            "email": person.email,
            "address": person.address,
            "notes": person.notes,
            "avatar_url": person.avatar_url,
            "tags": person.tags or [],
            "metadata": person.metadata or {},
            "created_at": datetime.now().isoformat()
        }
        
        # Create folder path
        folder_path = f"/app/data/memory/people/{person.name.lower().replace(' ', '_')}"
        
        cursor.execute("""
            INSERT INTO people (user_id, name, folder_path, profile, facts, 
                              important_dates, preferences)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, person.name, folder_path, json.dumps(profile),
            "{}", "{}", "{}"
        ))
        
        person_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Create folder
        Path(folder_path).mkdir(parents=True, exist_ok=True)
        
        return {"person": {"id": person_id, "name": person.name, "profile": profile}}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/people/{person_id}")
async def update_person(person_id: int, person: PersonUpdate, user_id: str = Query("default", description="User ID")):
    """Update a person"""
    try:
        conn = people_service._connect_db()
        cursor = conn.cursor()
        
        # Get existing person
        cursor.execute("SELECT profile FROM people WHERE id = ? AND user_id = ?", (person_id, user_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")
        
        # Update profile
        existing_profile = json.loads(row["profile"]) if row["profile"] else {}
        
        update_data = {}
        if person.name is not None:
            update_data["name"] = person.name
        if person.relationship is not None:
            existing_profile["relationship"] = person.relationship
        if person.birthday is not None:
            existing_profile["birthday"] = person.birthday
        if person.phone is not None:
            existing_profile["phone"] = person.phone
        if person.email is not None:
            existing_profile["email"] = person.email
        if person.address is not None:
            existing_profile["address"] = person.address
        if person.notes is not None:
            existing_profile["notes"] = person.notes
        if person.avatar_url is not None:
            existing_profile["avatar_url"] = person.avatar_url
        if person.tags is not None:
            existing_profile["tags"] = person.tags
        if person.metadata is not None:
            existing_profile["metadata"] = person.metadata
        
        existing_profile["updated_at"] = datetime.now().isoformat()
        
        # Update database
        cursor.execute("""
            UPDATE people 
            SET profile = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
        """, (json.dumps(existing_profile), person_id, user_id))
        
        conn.commit()
        conn.close()
        
        return {"message": "Person updated successfully", "person_id": person_id}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/people/{person_id}")
async def delete_person(person_id: int, user_id: str = Query("default", description="User ID")):
    """Delete a person"""
    try:
        conn = people_service._connect_db()
        cursor = conn.cursor()
        
        # Check if person exists
        cursor.execute("SELECT name FROM people WHERE id = ? AND user_id = ?", (person_id, user_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")
        
        # Delete person
        cursor.execute("DELETE FROM people WHERE id = ? AND user_id = ?", (person_id, user_id))
        
        # Delete related data
        cursor.execute("DELETE FROM relationships WHERE (person1_id = ? OR person2_id = ?) AND user_id = ?", 
                      (person_id, person_id, user_id))
        cursor.execute("DELETE FROM timeline_events WHERE person_id = ? AND user_id = ?", 
                      (person_id, user_id))
        
        conn.commit()
        conn.close()
        
        return {"message": f"Person '{row['name']}' deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/people/{person_id}/relationships")
async def get_person_relationships(person_id: int, user_id: str = Query("default", description="User ID")):
    """Get relationships for a person"""
    try:
        conn = people_service._connect_db()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT r.id, r.relationship_type, r.strength, r.notes, r.created_at,
                   p1.name as person1_name, p2.name as person2_name
            FROM relationships r
            JOIN people p1 ON r.person1_id = p1.id
            JOIN people p2 ON r.person2_id = p2.id
            WHERE (r.person1_id = ? OR r.person2_id = ?) AND r.user_id = ?
            ORDER BY r.strength DESC, r.created_at DESC
        """, (person_id, person_id, user_id))
        
        relationships = []
        for row in cursor.fetchall():
            relationships.append({
                "id": row["id"],
                "relationship_type": row["relationship_type"],
                "strength": row["strength"],
                "notes": row["notes"],
                "person1_name": row["person1_name"],
                "person2_name": row["person2_name"],
                "created_at": row["created_at"]
            })
        
        conn.close()
        return {"relationships": relationships, "count": len(relationships)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/relationships")
async def create_relationship(relationship: RelationshipCreate, user_id: str = Query("default", description="User ID")):
    """Create a relationship between two people"""
    try:
        conn = people_service._connect_db()
        cursor = conn.cursor()
        
        # Verify both people exist and belong to user
        cursor.execute("SELECT id FROM people WHERE id IN (?, ?) AND user_id = ?", 
                      (relationship.person1_id, relationship.person2_id, user_id))
        people = cursor.fetchall()
        if len(people) != 2:
            raise HTTPException(status_code=400, detail="One or both people not found")
        
        # Check if relationship already exists
        cursor.execute("""
            SELECT id FROM relationships 
            WHERE ((person1_id = ? AND person2_id = ?) OR (person1_id = ? AND person2_id = ?))
            AND user_id = ?
        """, (relationship.person1_id, relationship.person2_id, 
              relationship.person2_id, relationship.person1_id, user_id))
        
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Relationship already exists")
        
        # Create relationship
        cursor.execute("""
            INSERT INTO relationships (user_id, person1_id, person2_id, relationship_type, 
                                    strength, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id, relationship.person1_id, relationship.person2_id,
            relationship.relationship_type, relationship.strength,
            relationship.notes
        ))
        
        relationship_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {"relationship": {"id": relationship_id, "message": "Relationship created successfully"}}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/people/{person_id}/timeline")
async def get_person_timeline(person_id: int, user_id: str = Query("default", description="User ID")):
    """Get timeline events for a person"""
    try:
        conn = people_service._connect_db()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, event_type, event_title, event_description, event_date,
                   importance, metadata, created_at
            FROM timeline_events
            WHERE person_id = ? AND user_id = ?
            ORDER BY event_date DESC, importance DESC, created_at DESC
        """, (person_id, user_id))
        
        events = []
        for row in cursor.fetchall():
            events.append({
                "id": row["id"],
                "event_type": row["event_type"],
                "event_title": row["event_title"],
                "event_description": row["event_description"],
                "event_date": row["event_date"],
                "importance": row["importance"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "created_at": row["created_at"]
            })
        
        conn.close()
        return {"timeline": events, "count": len(events)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/timeline")
async def create_timeline_event(event: TimelineEventCreate, user_id: str = Query("default", description="User ID")):
    """Create a timeline event for a person"""
    try:
        conn = people_service._connect_db()
        cursor = conn.cursor()
        
        # Verify person exists and belongs to user
        cursor.execute("SELECT id FROM people WHERE id = ? AND user_id = ?", (event.person_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=400, detail="Person not found")
        
        # Create timeline event
        cursor.execute("""
            INSERT INTO timeline_events (user_id, person_id, event_type, event_title,
                                       event_description, event_date, importance, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, event.person_id, event.event_type, event.event_title,
            event.event_description, event.event_date, event.importance,
            json.dumps(event.metadata or {})
        ))
        
        event_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {"event": {"id": event_id, "message": "Timeline event created successfully"}}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/people/{person_id}/analysis")
async def analyze_person(person_id: int, user_id: str = Query("default", description="User ID")):
    """Get comprehensive analysis of a person including relationships and timeline"""
    try:
        conn = people_service._connect_db()
        cursor = conn.cursor()
        
        # Get person data
        cursor.execute("""
            SELECT id, name, profile, facts, important_dates, preferences,
                   created_at, updated_at
            FROM people 
            WHERE id = ? AND user_id = ?
        """, (person_id, user_id))
        
        person_row = cursor.fetchone()
        if not person_row:
            raise HTTPException(status_code=404, detail="Person not found")
        
        person = {
            "id": person_row["id"],
            "name": person_row["name"],
            "profile": json.loads(person_row["profile"]) if person_row["profile"] else {},
            "facts": json.loads(person_row["facts"]) if person_row["facts"] else {},
            "important_dates": json.loads(person_row["important_dates"]) if person_row["important_dates"] else {},
            "preferences": json.loads(person_row["preferences"]) if person_row["preferences"] else {},
            "created_at": person_row["created_at"],
            "updated_at": person_row["updated_at"]
        }
        
        # Get relationships
        cursor.execute("""
            SELECT r.relationship_type, r.strength, r.notes,
                   p1.name as person1_name, p2.name as person2_name
            FROM relationships r
            JOIN people p1 ON r.person1_id = p1.id
            JOIN people p2 ON r.person2_id = p2.id
            WHERE (r.person1_id = ? OR r.person2_id = ?) AND r.user_id = ?
            ORDER BY r.strength DESC
        """, (person_id, person_id, user_id))
        
        relationships = []
        for row in cursor.fetchall():
            relationships.append({
                "relationship_type": row["relationship_type"],
                "strength": row["strength"],
                "person1_name": row["person1_name"],
                "person2_name": row["person2_name"],
                "notes": row["notes"]
            })
        
        # Get recent timeline events
        cursor.execute("""
            SELECT event_type, event_title, event_description, event_date, importance
            FROM timeline_events
            WHERE person_id = ? AND user_id = ?
            ORDER BY event_date DESC, importance DESC
            LIMIT 10
        """, (person_id, user_id))
        
        recent_events = []
        for row in cursor.fetchall():
            recent_events.append({
                "event_type": row["event_type"],
                "event_title": row["event_title"],
                "event_description": row["event_description"],
                "event_date": row["event_date"],
                "importance": row["importance"]
            })
        
        conn.close()
        
        # Generate analysis
        analysis = {
            "person": person,
            "relationships": {
                "count": len(relationships),
                "strongest": relationships[0] if relationships else None,
                "types": list(set([r["relationship_type"] for r in relationships]))
            },
            "timeline": {
                "recent_events": recent_events,
                "total_events": len(recent_events)
            },
            "insights": {
                "relationship_strength_avg": sum([r["strength"] for r in relationships]) / len(relationships) if relationships else 0,
                "most_common_event_type": max(set([e["event_type"] for e in recent_events]), key=[e["event_type"] for e in recent_events].count) if recent_events else None
            }
        }
        
        return {"analysis": analysis}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
