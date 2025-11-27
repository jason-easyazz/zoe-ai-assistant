#!/usr/bin/env python3
"""
Migration script for Enhanced Lists System
Follows Zoe's database migration patterns
Runs on both Jetson and Pi platforms
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
if not os.path.exists(DB_PATH):
    # Try alternative path
    DB_PATH = os.getenv("DATABASE_PATH", "/home/zoe/assistant/data/zoe.db")

MIGRATION_VERSION = 2  # Increment for each migration

def check_migration_applied(conn):
    """Check if this migration has already been applied"""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT version FROM schema_migrations 
            WHERE version = ?
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

def migrate_lists_enhancements(conn):
    """Apply Enhanced Lists System migration"""
    cursor = conn.cursor()
    
    print("🔧 Starting Enhanced Lists migration...")
    
    try:
        # Check current schema
        cursor.execute("PRAGMA table_info(list_items)")
        columns = {col[1]: col for col in cursor.fetchall()}
        
        # Add parent_id for hierarchy
        if 'parent_id' not in columns:
            print("  ➜ Adding parent_id column...")
            try:
                cursor.execute("""
                    ALTER TABLE list_items 
                    ADD COLUMN parent_id INTEGER 
                    REFERENCES list_items(id) ON DELETE CASCADE
                """)
                print("  ✅ Added parent_id column")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    print("  ⚠️  parent_id column already exists")
                else:
                    raise
        else:
            print("  ℹ️  parent_id column already exists")
        
        # Add reminder_time
        if 'reminder_time' not in columns:
            print("  ➜ Adding reminder_time column...")
            try:
                cursor.execute("""
                    ALTER TABLE list_items 
                    ADD COLUMN reminder_time DATETIME
                """)
                print("  ✅ Added reminder_time column")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    print("  ⚠️  reminder_time column already exists")
                else:
                    raise
        else:
            print("  ℹ️  reminder_time column already exists")
        
        # Add repeat_pattern
        if 'repeat_pattern' not in columns:
            print("  ➜ Adding repeat_pattern column...")
            try:
                cursor.execute("""
                    ALTER TABLE list_items 
                    ADD COLUMN repeat_pattern TEXT
                """)
                print("  ✅ Added repeat_pattern column")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    print("  ⚠️  repeat_pattern column already exists")
                else:
                    raise
        else:
            print("  ℹ️  repeat_pattern column already exists")
        
        # Add repeat_end_date
        if 'repeat_end_date' not in columns:
            print("  ➜ Adding repeat_end_date column...")
            try:
                cursor.execute("""
                    ALTER TABLE list_items 
                    ADD COLUMN repeat_end_date DATE
                """)
                print("  ✅ Added repeat_end_date column")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    print("  ⚠️  repeat_end_date column already exists")
                else:
                    raise
        else:
            print("  ℹ️  repeat_end_date column already exists")
        
        # Add due_time
        if 'due_time' not in columns:
            print("  ➜ Adding due_time column...")
            try:
                cursor.execute("""
                    ALTER TABLE list_items 
                    ADD COLUMN due_time TIME
                """)
                print("  ✅ Added due_time column")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    print("  ⚠️  due_time column already exists")
                else:
                    raise
        else:
            print("  ℹ️  due_time column already exists")
        
        # Create indexes
        print("  ➜ Creating indexes...")
        
        # Index for parent_id
        try:
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_list_items_parent_id 
                ON list_items(parent_id)
            """)
            print("  ✅ Created idx_list_items_parent_id")
        except sqlite3.OperationalError as e:
            print(f"  ⚠️  Index creation: {e}")
        
        # Index for reminder_time
        try:
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_list_items_reminder_time 
                ON list_items(reminder_time)
            """)
            print("  ✅ Created idx_list_items_reminder_time")
        except sqlite3.OperationalError as e:
            print(f"  ⚠️  Index creation: {e}")
        
        # Index for due_date and due_time
        try:
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_list_items_due_date_time 
                ON list_items(due_date, due_time)
            """)
            print("  ✅ Created idx_list_items_due_date_time")
        except sqlite3.OperationalError as e:
            print(f"  ⚠️  Index creation: {e}")
        
        # Record migration
        cursor.execute("""
            INSERT OR IGNORE INTO schema_migrations (version, name) 
            VALUES (?, ?)
        """, (MIGRATION_VERSION, 'enhance_lists_system'))
        
        conn.commit()
        print("✅ Migration completed successfully!")
        return True
        
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print(f"⚠️  Column already exists: {e}")
            print("  ℹ️  Marking migration as applied...")
            cursor.execute("""
                INSERT OR IGNORE INTO schema_migrations (version, name) 
                VALUES (?, ?)
            """, (MIGRATION_VERSION, 'enhance_lists_system'))
            conn.commit()
            return True
        else:
            print(f"❌ Migration failed: {e}")
            conn.rollback()
            return False
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        conn.rollback()
        return False

def main():
    """Run migration with platform detection"""
    global DB_PATH  # Move global declaration to the top
    
    print("=" * 60)
    print("Enhanced Lists System - Database Migration")
    print("=" * 60)
    
    # Platform detection
    platform = os.getenv('HARDWARE_PLATFORM', 'unknown')
    print(f"Platform: {platform}")
    print(f"Database: {DB_PATH}")
    print()
    
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at {DB_PATH}")
        print("   Trying alternative path...")
        alt_path = "/home/zoe/assistant/data/zoe.db"
        if os.path.exists(alt_path):
            DB_PATH = alt_path
            print(f"✅ Found database at {alt_path}")
        else:
            return False
    
    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    
    try:
        # Create migration tracking table
        create_migration_table(conn)
        
        # Check if already applied
        if check_migration_applied(conn):
            print("ℹ️  Migration already applied. Skipping.")
            return True
        
        # Run migration
        success = migrate_lists_enhancements(conn)
        
        if success:
            print()
            print("✅ All migrations completed successfully!")
            print()
            print("Next steps:")
            print("  1. Restart zoe-core: docker compose restart zoe-core")
            print("  2. Test API: curl http://localhost:8000/api/lists/shopping")
            print("  3. Check logs: docker logs zoe-core --tail 50")
        
        return success
        
    finally:
        conn.close()

if __name__ == "__main__":
    exit(0 if main() else 1)

