import asyncio
import aiosqlite
from pathlib import Path

async def add_profiles_tables():
    """Add people and projects tables to existing database"""
    db_path = "/app/data/zoe.db"
    
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        
        # People table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT 'default',
                name TEXT NOT NULL UNIQUE,
                relationship TEXT DEFAULT 'friend',
                avatar_emoji TEXT DEFAULT '👤',
                summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_mentioned TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                mention_count INTEGER DEFAULT 0
            )
        """)
        
        # Projects table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT 'default',
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                category TEXT DEFAULT 'personal',
                status TEXT DEFAULT 'active',
                icon_emoji TEXT DEFAULT '📁',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                item_count INTEGER DEFAULT 0
            )
        """)
        
        # Enhanced memories table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT 'default',
                content TEXT NOT NULL,
                memory_type TEXT DEFAULT 'general',
                importance INTEGER DEFAULT 5,
                person_id INTEGER,
                project_id INTEGER,
                source TEXT DEFAULT 'chat',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 0,
                FOREIGN KEY (person_id) REFERENCES people (id),
                FOREIGN KEY (project_id) REFERENCES projects (id)
            )
        """)
        
        # Person attributes
        await db.execute("""
            CREATE TABLE IF NOT EXISTS person_attributes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER,
                category TEXT NOT NULL,
                attribute_key TEXT NOT NULL,
                attribute_value TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (person_id) REFERENCES people (id),
                UNIQUE(person_id, category, attribute_key)
            )
        """)
        
        # Project items
        await db.execute("""
            CREATE TABLE IF NOT EXISTS project_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                item_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                url TEXT,
                tags TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id)
            )
        """)
        
        # Add foreign keys to existing tables
        try:
            await db.execute("ALTER TABLE tasks ADD COLUMN person_id INTEGER")
            await db.execute("ALTER TABLE tasks ADD COLUMN project_id INTEGER")
            await db.execute("ALTER TABLE events ADD COLUMN person_id INTEGER") 
            await db.execute("ALTER TABLE events ADD COLUMN project_id INTEGER")
        except:
            pass  # Columns might already exist
        
        await db.commit()
        print("✅ Database schema updated successfully")

if __name__ == "__main__":
    asyncio.run(add_profiles_tables())
