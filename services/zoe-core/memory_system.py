"""
Zoe Memory System - Dynamic People, Projects & Relationships
ENHANCED: Connection pooling, WAL mode, proper indexes, concurrency guards
"""
import sqlite3
import json
from datetime import datetime
from typing import Dict, List, Optional
import os
from pathlib import Path
import threading
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

class ConnectionPool:
    """Thread-safe SQLite connection pool with WAL mode and optimized pragmas"""
    
    def __init__(self, db_path: str, max_connections: int = 5):
        self.db_path = db_path
        self.max_connections = max_connections
        self._connections = []
        self._lock = threading.Lock()
        self._local = threading.local()
        
    def _create_connection(self) -> sqlite3.Connection:
        """Create a new connection with optimized settings"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL")
        
        # Optimize for performance
        conn.execute("PRAGMA synchronous=NORMAL")  # Faster than FULL, still safe with WAL
        conn.execute("PRAGMA cache_size=-64000")   # 64MB cache
        conn.execute("PRAGMA temp_store=MEMORY")   # Use memory for temp tables
        conn.execute("PRAGMA mmap_size=268435456") # 256MB memory-mapped I/O
        
        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys=ON")
        
        return conn
    
    @contextmanager
    def get_connection(self):
        """Get a connection from the pool (context manager)"""
        # Try to get thread-local connection first
        if hasattr(self._local, 'connection') and self._local.connection:
            yield self._local.connection
            return
            
        with self._lock:
            # Try to reuse existing connection
            if self._connections:
                conn = self._connections.pop()
            else:
                conn = self._create_connection()
                logger.debug(f"Created new database connection (pool size: {len(self._connections)})")
        
        try:
            self._local.connection = conn
            yield conn
        finally:
            # Return connection to pool
            self._local.connection = None
            with self._lock:
                if len(self._connections) < self.max_connections:
                    self._connections.append(conn)
                else:
                    conn.close()
    
    def close_all(self):
        """Close all pooled connections"""
        with self._lock:
            for conn in self._connections:
                conn.close()
            self._connections.clear()


class MemorySystem:
    def __init__(self, db_path="/app/data/memory.db"):
        self.db_path = db_path
        self.memory_dir = Path("/app/data/memory")
        self.pool = ConnectionPool(db_path, max_connections=5)
        self.init_database()
        self.init_folders()
    
    def init_database(self):
        """Initialize memory database with proper indexes"""
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            
            # People profiles
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS people (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    folder_path TEXT,
                    profile TEXT,  -- JSON stored as TEXT
                    facts TEXT,    -- JSON stored as TEXT
                    important_dates TEXT,  -- JSON stored as TEXT
                    preferences TEXT,  -- JSON stored as TEXT
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for people
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_name ON people(name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_updated ON people(updated_at DESC)")
            
            # Projects
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    folder_path TEXT,
                    description TEXT,
                    status TEXT DEFAULT 'active',
                    metadata TEXT,  -- JSON stored as TEXT
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for projects
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at DESC)")
            
            # Relationships
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person1_id INTEGER,
                    person2_id INTEGER,
                    relationship_type TEXT,
                    metadata TEXT,  -- JSON stored as TEXT
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (person1_id) REFERENCES people(id) ON DELETE CASCADE,
                    FOREIGN KEY (person2_id) REFERENCES people(id) ON DELETE CASCADE
                )
            """)
            
            # Create indexes for relationships
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_p1 ON relationships(person1_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_p2 ON relationships(person2_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_relationships_type ON relationships(relationship_type)")
            
            # Memory facts (searchable)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memory_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,  -- 'person', 'project', 'general'
                    entity_id INTEGER,
                    fact TEXT NOT NULL,
                    category TEXT,
                    importance INTEGER DEFAULT 5,
                    source TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for memory_facts
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_entity ON memory_facts(entity_type, entity_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_category ON memory_facts(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_importance ON memory_facts(importance DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_created ON memory_facts(created_at DESC)")
            
            # Full-text search for facts (SQLite FTS5)
            try:
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_facts_fts 
                    USING fts5(fact, content='memory_facts', content_rowid='id')
                """)
                logger.info("✅ Full-text search enabled for memory facts")
            except sqlite3.OperationalError as e:
                logger.warning(f"⚠️ FTS5 not available, using LIKE search: {e}")
            
            conn.commit()
    
    def init_folders(self):
        """Create folder structure"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        (self.memory_dir / "people").mkdir(exist_ok=True)
        (self.memory_dir / "projects").mkdir(exist_ok=True)
        (self.memory_dir / "relationships").mkdir(exist_ok=True)
    
    def add_person(self, name: str, initial_facts: List[str] = None) -> Dict:
        """Add a new person to memory"""
        with self.pool.get_connection() as conn:
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
            
            return {
                "id": person_id,
                "name": name,
                "folder": str(person_folder),
                "facts_added": len(initial_facts) if initial_facts else 0
            }
    
    def add_project(self, name: str, description: str = "") -> Dict:
        """Add a new project to memory"""
        with self.pool.get_connection() as conn:
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
            
            return {
                "id": project_id,
                "name": name,
                "folder": str(project_folder),
                "description": description
            }
    
    def add_relationship(self, person1: str, person2: str, 
                        relationship: str) -> Dict:
        """Add relationship between people"""
        with self.pool.get_connection() as conn:
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
                
                return {
                    "success": True,
                    "relationship": f"{person1} is {relationship} of {person2}"
                }
            
            return {"success": False, "error": "Person not found"}
    
    def search_memories(self, query: str) -> List[Dict]:
        """Search all memories using FTS5 if available, otherwise LIKE"""
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            
            # Try FTS5 first
            try:
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
                        mf.created_at,
                        rank
                    FROM memory_facts_fts 
                    JOIN memory_facts mf ON memory_facts_fts.rowid = mf.id
                    LEFT JOIN people p ON mf.entity_type = 'person' AND mf.entity_id = p.id
                    LEFT JOIN projects pr ON mf.entity_type = 'project' AND mf.entity_id = pr.id
                    WHERE memory_facts_fts MATCH ?
                    ORDER BY rank, mf.importance DESC
                    LIMIT 10
                """, (query,))
            except sqlite3.OperationalError:
                # Fall back to LIKE search
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
                        mf.created_at,
                        0 as rank
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
            
            return results
    
    def get_person_context(self, name: str) -> Dict:
        """Get all context about a person"""
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get person details
            cursor.execute("""
                SELECT id, profile, facts, important_dates, preferences
                FROM people WHERE name = ?
            """, (name,))
            
            person = cursor.fetchone()
            if not person:
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
            
            return {
                "found": True,
                "name": name,
                "profile": json.loads(person[1]) if person[1] else {},
                "facts": facts,
                "important_dates": json.loads(person[3]) if person[3] else [],
                "preferences": json.loads(person[4]) if person[4] else {},
                "relationships": relationships
            }
    
    def close(self):
        """Close all database connections"""
        self.pool.close_all()
    
    def __del__(self):
        """Cleanup on deletion"""
        try:
            self.close()
        except:
            pass
