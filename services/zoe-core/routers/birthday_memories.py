"""
Birthday Memories Endpoint
==========================

A simple endpoint for adding people with birthdays to memories
without requiring full authentication - used by the birthday expert
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
import sqlite3
import json
import os

router = APIRouter(prefix="/api/birthday-memories", tags=["birthday-memories"])

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

class BirthdayPersonCreate(BaseModel):
    name: str
    birthday: str
    relationship: str = "family"
    user_id: str = "default"
    notes: str = ""
    metadata: Dict[str, Any] = {}

@router.post("/add-person")
async def add_birthday_person(person: BirthdayPersonCreate):
    """Add a person with birthday to memories (simplified endpoint for birthday expert)"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Ensure people table exists
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
        
        # Insert person
        cursor.execute("""
            INSERT INTO people (user_id, name, relationship, birthday, notes, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            person.user_id,
            person.name,
            person.relationship,
            person.birthday,
            person.notes,
            json.dumps(person.metadata)
        ))
        
        person_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "person_id": person_id,
            "message": f"✅ Added {person.name} to memories with birthday {person.birthday}"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"❌ Failed to add {person.name} to memories"
        }

@router.get("/people")
async def get_birthday_people(user_id: str = "default"):
    """Get all people with birthdays from memories"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, relationship, birthday, notes, metadata, created_at
            FROM people 
            WHERE user_id = ? AND birthday IS NOT NULL
            ORDER BY name
        """, (user_id,))
        
        people = []
        for row in cursor.fetchall():
            people.append({
                "id": row[0],
                "name": row[1],
                "relationship": row[2],
                "birthday": row[3],
                "notes": row[4],
                "metadata": json.loads(row[5]) if row[5] else {},
                "created_at": row[6]
            })
        
        conn.close()
        
        return {
            "success": True,
            "people": people,
            "count": len(people)
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "people": [],
            "count": 0
        }
