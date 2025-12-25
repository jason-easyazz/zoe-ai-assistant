#!/usr/bin/env python3
"""Recreate missing database tables"""
import sqlite3

DB_PATH = "/app/data/zoe.db"

def main():
    print("🔧 Recreating missing tables...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create lists table
    print("  Creating lists table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lists (
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
    
    # Create list_items table
    print("  Creating list_items table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS list_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL,
            task_text TEXT NOT NULL,
            priority TEXT DEFAULT 'medium',
            completed BOOLEAN DEFAULT 0,
            completed_at TIMESTAMP,
            metadata JSON,
            journey_id INTEGER,
            due_date DATE,
            transaction_id INTEGER,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE
        )
    """)
    
    # Create memories table
    print("  Creating memories table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            title TEXT,
            content TEXT NOT NULL,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create indexes
    print("  Creating indexes...")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lists_category ON lists(list_category, user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_list_items_list_id ON list_items(list_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_user_id ON memories(user_id)")
    
    conn.commit()
    
    # Verify
    print("\n✅ Verification:")
    for table in ['lists', 'list_items', 'memories']:
        cursor.execute(f'SELECT COUNT(*) FROM {table}')
        count = cursor.fetchone()[0]
        print(f'  - {table}: {count} rows')
    
    conn.close()
    print("\n✅ All tables recreated successfully!")

if __name__ == "__main__":
    main()






