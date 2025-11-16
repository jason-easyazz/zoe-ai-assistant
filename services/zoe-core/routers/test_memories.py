"""
Test Memories Endpoint
======================

A simple endpoint to test memories without authentication
"""

from fastapi import APIRouter, Query
from typing import Dict, Any
import sqlite3
import json

router = APIRouter(prefix="/api/test-memories", tags=["test-memories"])

@router.get("/people")
async def get_test_people():
    """Get people from memories without authentication (for testing)"""
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, relationship, birthday, phone, email, address, notes,
                   avatar_url, tags, metadata, created_at, updated_at
            FROM people 
            WHERE user_id = 'default'
            ORDER BY name
        """)
        
        people = []
        for row in cursor.fetchall():
            people.append({
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
        
        conn.close()
        
        return {
            "memories": people,
            "count": len(people)
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "memories": [],
            "count": 0
        }
