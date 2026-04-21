#!/usr/bin/env python3
"""
Migration Script: Move list items from JSON column to separate table
Fixes the issue where MCP tools create lists but frontend can't see items
"""

import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.getenv("DATABASE_PATH", os.getenv("DATABASE_PATH", str(Path(__file__).parent.parent.parent.resolve() / "data" / "zoe.db")))

def migrate_lists_items():
    """Migrate list items from JSON storage to separate table"""
    
    print("Starting migration: JSON items → list_items table")
    print(f"Database: {DB_PATH}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if items column exists in lists table
    cursor.execute("PRAGMA table_info(lists)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'items' not in columns:
        print("✓ Migration already complete - 'items' column doesn't exist")
        conn.close()
        return
    
    # Get all lists that have JSON items
    cursor.execute("SELECT id, items FROM lists WHERE items IS NOT NULL AND items != ''")
    lists_with_items = cursor.fetchall()
    
    print(f"Found {len(lists_with_items)} lists with JSON items")
    
    migrated_count = 0
    item_count = 0
    
    for list_id, items_json in lists_with_items:
        try:
            items = json.loads(items_json)
            if not isinstance(items, list):
                print(f"⚠ List {list_id}: items is not a list, skipping")
                continue
                
            # Check if items already exist in list_items table
            cursor.execute("SELECT COUNT(*) FROM list_items WHERE list_id = ?", (list_id,))
            existing_count = cursor.fetchone()[0]
            
            if existing_count > 0:
                print(f"⚠ List {list_id}: Already has {existing_count} items in list_items table, skipping")
                continue
            
            # Insert each item into list_items table
            for item in items:
                if isinstance(item, dict):
                    task_text = item.get('text', '')
                    priority = item.get('priority', 'medium')
                    completed = item.get('completed', False)
                    
                    if task_text:
                        cursor.execute("""
                            INSERT INTO list_items (list_id, task_text, priority, completed, created_at, updated_at)
                            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """, (list_id, task_text, priority, 1 if completed else 0))
                        item_count += 1
            
            migrated_count += 1
            print(f"✓ Migrated list {list_id}: {len(items)} items")
            
        except json.JSONDecodeError:
            print(f"⚠ List {list_id}: Invalid JSON, skipping")
        except Exception as e:
            print(f"✗ List {list_id}: Error - {str(e)}")
    
    # Now we can safely drop the items column
    try:
        print("\nDropping old 'items' column...")
        
        # SQLite doesn't support DROP COLUMN directly, so we need to recreate the table
        cursor.execute("""
            CREATE TABLE lists_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT 'default',
                list_type TEXT NOT NULL,
                list_category TEXT DEFAULT 'personal',
                name TEXT NOT NULL,
                description TEXT,
                metadata JSON,
                shared_with JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            INSERT INTO lists_new (id, user_id, list_type, list_category, name, description, metadata, shared_with, created_at, updated_at)
            SELECT id, user_id, list_type, list_category, name, description, metadata, shared_with, created_at, updated_at
            FROM lists
        """)
        
        cursor.execute("DROP TABLE lists")
        cursor.execute("ALTER TABLE lists_new RENAME TO lists")
        
        # Recreate indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lists_category 
            ON lists(list_category, user_id)
        """)
        
        print("✓ Successfully dropped 'items' column")
        
    except Exception as e:
        print(f"⚠ Could not drop 'items' column: {str(e)}")
        print("  (This is okay - the column will be ignored)")
    
    conn.commit()
    conn.close()
    
    print(f"\n{'='*60}")
    print(f"Migration Complete!")
    print(f"{'='*60}")
    print(f"Lists migrated: {migrated_count}")
    print(f"Total items migrated: {item_count}")
    print(f"{'='*60}")

if __name__ == "__main__":
    try:
        migrate_lists_items()
    except Exception as e:
        print(f"Migration failed: {str(e)}")
        import traceback
        traceback.print_exc()
        exit(1)


