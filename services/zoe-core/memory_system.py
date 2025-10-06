"""
Zoe Memory System - Dynamic People, Projects & Relationships
"""
import sqlite3
import json
from datetime import datetime
from typing import Dict, List, Optional
import os
from pathlib import Path

class MemorySystem:
    def __init__(self, db_path="/app/data/memory.db"):
        self.db_path = db_path
        self.memory_dir = Path("/app/data/memory")
        self.init_database()
        self.init_folders()
    
    def init_database(self):
        """Initialize memory database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # People profiles
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                folder_path TEXT,
                profile JSON,
                facts JSON,
                important_dates JSON,
                preferences JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Projects
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                folder_path TEXT,
                description TEXT,
                status TEXT DEFAULT 'active',
                metadata JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Relationships
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person1_id INTEGER,
                person2_id INTEGER,
                relationship_type TEXT,
                metadata JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (person1_id) REFERENCES people(id),
                FOREIGN KEY (person2_id) REFERENCES people(id)
            )
        """)
        
        # Memory facts (searchable)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT, -- 'person', 'project', 'general'
                entity_id INTEGER,
                fact TEXT NOT NULL,
                category TEXT,
                importance INTEGER DEFAULT 5,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
    
    def init_folders(self):
        """Create folder structure"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        (self.memory_dir / "people").mkdir(exist_ok=True)
        (self.memory_dir / "projects").mkdir(exist_ok=True)
        (self.memory_dir / "relationships").mkdir(exist_ok=True)
    
    def add_person(self, name: str, initial_facts: List[str] = None) -> Dict:
        """Add a new person to memory"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create folder
        person_folder = self.memory_dir / "people" / name.lower().replace(" ", "_")
        person_folder.mkdir(exist_ok=True)
        
        # Create profile
        profile = {
            "name": name,
            "first_mentioned": datetime.now().isoformat(),
            "interaction_count": 1
        }
        
        cursor.execute("""
            INSERT OR IGNORE INTO people (name, folder_path, profile)
            VALUES (?, ?, ?)
        """, (name, str(person_folder), json.dumps(profile)))
        
        person_id = cursor.lastrowid
        
        # Add initial facts
        if initial_facts:
            for fact in initial_facts:
                cursor.execute("""
                    INSERT INTO memory_facts (entity_type, entity_id, fact, category)
                    VALUES ('person', ?, ?, 'general')
                """, (person_id, fact))
        
        conn.commit()
        conn.close()
        
        return {
            "id": person_id,
            "name": name,
            "folder": str(person_folder),
            "facts_added": len(initial_facts) if initial_facts else 0
        }
    
    def add_project(self, name: str, description: str = "") -> Dict:
        """Add a new project to memory"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create folder
        project_folder = self.memory_dir / "projects" / name.lower().replace(" ", "_")
        project_folder.mkdir(exist_ok=True)
        
        cursor.execute("""
            INSERT OR IGNORE INTO projects (name, folder_path, description)
            VALUES (?, ?, ?)
        """, (name, str(project_folder), description))
        
        project_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {
            "id": project_id,
            "name": name,
            "folder": str(project_folder),
            "description": description
        }
    
    def add_relationship(self, person1: str, person2: str, 
                        relationship: str) -> Dict:
        """Add relationship between people"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get person IDs
        cursor.execute("SELECT id FROM people WHERE name = ?", (person1,))
        p1 = cursor.fetchone()
        
        cursor.execute("SELECT id FROM people WHERE name = ?", (person2,))
        p2 = cursor.fetchone()
        
        if p1 and p2:
            cursor.execute("""
                INSERT INTO relationships (person1_id, person2_id, relationship_type)
                VALUES (?, ?, ?)
            """, (p1[0], p2[0], relationship))
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "relationship": f"{person1} is {relationship} of {person2}"
            }
        
        conn.close()
        return {"success": False, "error": "Person not found"}
    
    def search_memories(self, query: str) -> List[Dict]:
        """Search all memories"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Search facts
        cursor.execute("""
            SELECT 
                mf.fact,
                mf.entity_type,
                CASE 
                    WHEN mf.entity_type = 'person' THEN p.name
                    WHEN mf.entity_type = 'project' THEN pr.name
                    ELSE 'General'
                END as entity_name,
                mf.importance,
                mf.created_at
            FROM memory_facts mf
            LEFT JOIN people p ON mf.entity_type = 'person' AND mf.entity_id = p.id
            LEFT JOIN projects pr ON mf.entity_type = 'project' AND mf.entity_id = pr.id
            WHERE mf.fact LIKE ?
            ORDER BY mf.importance DESC, mf.created_at DESC
            LIMIT 10
        """, (f"%{query}%",))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                "fact": row[0],
                "type": row[1],
                "entity": row[2],
                "importance": row[3],
                "date": row[4]
            })
        
        conn.close()
        return results
    
    def get_person_context(self, name: str) -> Dict:
        """Get all context about a person"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get person details
        cursor.execute("""
            SELECT id, profile, facts, important_dates, preferences
            FROM people WHERE name = ?
        """, (name,))
        
        person = cursor.fetchone()
        if not person:
            conn.close()
            return {"found": False}
        
        person_id = person[0]
        
        # Get all facts
        cursor.execute("""
            SELECT fact, category, importance
            FROM memory_facts
            WHERE entity_type = 'person' AND entity_id = ?
            ORDER BY importance DESC
        """, (person_id,))
        
        facts = [{"fact": row[0], "category": row[1], "importance": row[2]} 
                 for row in cursor.fetchall()]
        
        # Get relationships
        cursor.execute("""
            SELECT 
                p2.name,
                r.relationship_type
            FROM relationships r
            JOIN people p2 ON r.person2_id = p2.id
            WHERE r.person1_id = ?
        """, (person_id,))
        
        relationships = [{"person": row[0], "relationship": row[1]} 
                        for row in cursor.fetchall()]
        
        conn.close()
        
        return {
            "found": True,
            "name": name,
            "profile": json.loads(person[1]) if person[1] else {},
            "facts": facts,
            "important_dates": json.loads(person[3]) if person[3] else [],
            "preferences": json.loads(person[4]) if person[4] else {},
            "relationships": relationships
        }
