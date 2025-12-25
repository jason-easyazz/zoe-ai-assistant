#!/usr/bin/env python3
"""Add missing columns to list_items table"""
import sqlite3

DB_PATH = "/app/data/zoe.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("🔧 Adding missing columns to list_items...")
    
    columns = [
        ("parent_id", "INTEGER"),
        ("reminder_time", "DATETIME"),
        ("repeat_pattern", "TEXT"),
        ("repeat_end_date", "DATE"),
        ("due_time", "TIME"),
        ("item_name", "TEXT"),
        ("archived", "BOOLEAN DEFAULT 0"),
        ("archived_at", "TIMESTAMP"),
        ("notes", "TEXT"),
        ("tags", "TEXT"),
        ("assigned_to", "TEXT"),
        ("user_id", "TEXT")
    ]
    
    for col_name, col_type in columns:
        try:
            cursor.execute(f"ALTER TABLE list_items ADD COLUMN {col_name} {col_type}")
            print(f"  ✅ Added: {col_name}")
        except Exception as e:
            if "duplicate" in str(e).lower():
                print(f"  ⏭️  Exists: {col_name}")
            else:
                print(f"  ❌ Error: {col_name} - {e}")
    
    conn.commit()
    conn.close()
    print("\n✅ Schema updated for advanced lists!")

if __name__ == "__main__":
    main()






