#!/usr/bin/env python3
"""
Migration script to update lists table schema
Adds missing columns: list_type, list_category, items (JSON), metadata (JSON), shared_with (JSON)
"""
import sqlite3
import json
import os

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

def migrate_lists_schema():
    """Migrate lists table to new schema"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Check current schema
        cursor.execute("PRAGMA table_info(lists)")
        columns = {col[1]: col for col in cursor.fetchall()}
        print(f"Current columns: {list(columns.keys())}")
        
        # Add list_type column if missing (based on category)
        if 'list_type' not in columns:
            print("Adding list_type column...")
            cursor.execute("""
                ALTER TABLE lists 
                ADD COLUMN list_type TEXT DEFAULT 'personal_todos'
            """)
            
            # Update list_type based on category
            cursor.execute("""
                UPDATE lists 
                SET list_type = CASE 
                    WHEN category = 'shopping' THEN 'shopping'
                    WHEN category = 'bucket' THEN 'bucket'
                    WHEN category = 'work' THEN 'work_todos'
                    ELSE 'personal_todos'
                END
            """)
            print("✅ Added list_type column")
        
        # Rename category to list_category if needed
        if 'category' in columns and 'list_category' not in columns:
            print("Renaming category to list_category...")
            # SQLite doesn't support RENAME COLUMN in old versions, so we create new column
            cursor.execute("""
                ALTER TABLE lists 
                ADD COLUMN list_category TEXT DEFAULT 'personal'
            """)
            cursor.execute("UPDATE lists SET list_category = category")
            print("✅ Added list_category column")
        
        # Add items column if missing
        if 'items' not in columns:
            print("Adding items column...")
            cursor.execute("""
                ALTER TABLE lists 
                ADD COLUMN items JSON DEFAULT '[]'
            """)
            
            # Migrate data from list_items table if it exists
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='list_items'
            """)
            if cursor.fetchone():
                print("Migrating items from list_items table...")
                cursor.execute("""
                    SELECT id FROM lists
                """)
                list_ids = cursor.fetchall()
                
                for (list_id,) in list_ids:
                    cursor.execute("""
                        SELECT id, task_text, priority, completed, completed_at, created_at
                        FROM list_items 
                        WHERE list_id = ?
                    """, (list_id,))
                    
                    items = []
                    for item in cursor.fetchall():
                        items.append({
                            "id": item[0],
                            "text": item[1],
                            "priority": item[2] or "medium",
                            "completed": bool(item[3]),
                            "completed_at": item[4],
                            "created_at": item[5]
                        })
                    
                    cursor.execute("""
                        UPDATE lists 
                        SET items = ? 
                        WHERE id = ?
                    """, (json.dumps(items), list_id))
                
                print("✅ Migrated items from list_items table")
            print("✅ Added items column")
        
        # Add metadata column if missing
        if 'metadata' not in columns:
            print("Adding metadata column...")
            cursor.execute("""
                ALTER TABLE lists 
                ADD COLUMN metadata JSON DEFAULT '{}'
            """)
            print("✅ Added metadata column")
        
        # Add shared_with column if missing
        if 'shared_with' not in columns:
            print("Adding shared_with column...")
            cursor.execute("""
                ALTER TABLE lists 
                ADD COLUMN shared_with JSON DEFAULT '[]'
            """)
            print("✅ Added shared_with column")
        
        conn.commit()
        print("\n✅ Migration completed successfully!")
        
        # Show final schema
        cursor.execute("PRAGMA table_info(lists)")
        print("\nFinal schema:")
        for col in cursor.fetchall():
            print(f"  - {col[1]} ({col[2]})")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_lists_schema()

