#!/usr/bin/env python3
"""
Migration script for Project Lists with Stages
Adds support for multi-stage project workflows
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv('DATABASE_PATH', '/app/data/zoe.db')
MIGRATION_VERSION = 3  # Increment from lists enhancements

def check_migration_applied(conn):
    """Check if this migration has already been applied"""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT version FROM schema_migrations WHERE version = ?
        """, (MIGRATION_VERSION,))
        return cursor.fetchone() is not None
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return False

def create_migration_table(conn):
    """Create schema_migrations table if it doesn't exist"""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

def migrate_project_lists(conn):
    """Apply Project Lists migration"""
    cursor = conn.cursor()
    print("🔧 Starting Project Lists migration...")
    
    try:
        # Create project_lists table
        print("  ➜ Creating project_lists table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS project_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                current_stage_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create project_stages table
        print("  ➜ Creating project_stages table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS project_stages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                stage_order INTEGER NOT NULL,
                completed BOOLEAN DEFAULT FALSE,
                completed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES project_lists(id) ON DELETE CASCADE
            )
        """)
        
        # Create project_items table
        print("  ➜ Creating project_items table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS project_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage_id INTEGER NOT NULL,
                task_text TEXT NOT NULL,
                completed BOOLEAN DEFAULT FALSE,
                completed_at TIMESTAMP,
                priority TEXT DEFAULT 'medium',
                parent_id INTEGER,
                reminder_time DATETIME,
                due_date DATE,
                due_time TIME,
                repeat_pattern TEXT,
                repeat_end_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (stage_id) REFERENCES project_stages(id) ON DELETE CASCADE,
                FOREIGN KEY (parent_id) REFERENCES project_items(id) ON DELETE CASCADE
            )
        """)
        
        # Create indexes
        print("  ➜ Creating indexes...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_project_lists_user_id 
            ON project_lists(user_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_project_stages_project_id 
            ON project_stages(project_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_project_items_stage_id 
            ON project_items(stage_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_project_items_parent_id 
            ON project_items(parent_id)
        """)
        
        # Record migration
        cursor.execute("""
            INSERT INTO schema_migrations (version, name)
            VALUES (?, ?)
        """, (MIGRATION_VERSION, 'project_lists_with_stages'))
        
        conn.commit()
        print("✅ Migration completed successfully!")
        return True
        
    except sqlite3.OperationalError as e:
        if "already exists" in str(e):
            print(f"⚠️ Table already exists: {e}")
            print("  ℹ️ Marking migration as applied...")
            cursor.execute("""
                INSERT OR IGNORE INTO schema_migrations (version, name)
                VALUES (?, ?)
            """, (MIGRATION_VERSION, 'project_lists_with_stages'))
            conn.commit()
            return True
        else:
            print(f"❌ Migration failed: {e}")
            conn.rollback()
            return False

def main():
    """Run migration"""
    print("=" * 60)
    print("Project Lists with Stages - Database Migration")
    print("=" * 60)
    
    # Platform detection
    platform = os.getenv('HARDWARE_PLATFORM', 'unknown')
    print(f"Platform: {platform}")
    print(f"Database: {DB_PATH}")
    print()
    
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at {DB_PATH}")
        return False
    
    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    try:
        # Create migration tracking table
        create_migration_table(conn)
        
        # Check if already applied
        if check_migration_applied(conn):
            print("ℹ️ Migration already applied. Skipping.")
            return True
        
        # Run migration
        success = migrate_project_lists(conn)
        
        if success:
            print()
            print("✅ All migrations completed successfully!")
            print()
            print("Next steps:")
            print("  1. Restart zoe-core: docker compose restart zoe-core")
            print("  2. Test API: curl http://localhost:8000/api/projects")
            print("  3. Check logs: docker logs zoe-core --tail 50")
        
        return success
        
    finally:
        conn.close()

if __name__ == "__main__":
    exit(0 if main() else 1)

