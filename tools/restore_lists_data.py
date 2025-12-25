#!/usr/bin/env python3
"""Restore lists and list items for real users only"""
import sqlite3

BACKUP_DB = "/home/zoe/assistant/backups/pre_lists_enhancement_20251122_183136/zoe.db"
CURRENT_DB = "/app/data/zoe.db"

def main():
    # Real users to restore (skip test data)
    REAL_USERS = ['jason', 'andrew', 'teneeka', 'asya']
    
    print("📖 Reading lists from backup...")
    backup_conn = sqlite3.connect(BACKUP_DB)
    backup_cursor = backup_conn.cursor()
    
    # Get lists for real users
    placeholders = ','.join('?' * len(REAL_USERS))
    backup_cursor.execute(f"""
        SELECT id, user_id, list_type, list_category, name, description, metadata, shared_with, created_at, updated_at
        FROM lists 
        WHERE user_id IN ({placeholders})
    """, REAL_USERS)
    lists = backup_cursor.fetchall()
    
    print(f"✅ Found {len(lists)} lists for real users")
    
    # Get list_items for those lists
    list_ids = [row[0] for row in lists]
    if list_ids:
        placeholders = ','.join('?' * len(list_ids))
        backup_cursor.execute(f"""
            SELECT id, list_id, task_text, priority, completed, completed_at, metadata, journey_id, created_at, updated_at
            FROM list_items 
            WHERE list_id IN ({placeholders})
        """, list_ids)
        items = backup_cursor.fetchall()
        print(f"✅ Found {len(items)} items for those lists")
    else:
        items = []
    
    backup_conn.close()
    
    # Write to current database
    print("\n💾 Restoring to current database...")
    current_conn = sqlite3.connect(CURRENT_DB)
    current_cursor = current_conn.cursor()
    
    # Clear existing lists
    current_cursor.execute("DELETE FROM list_items")
    current_cursor.execute("DELETE FROM lists")
    print("🧹 Cleared existing lists")
    
    # Restore lists
    for list_row in lists:
        current_cursor.execute("""
            INSERT INTO lists 
            (id, user_id, list_type, list_category, name, description, metadata, shared_with, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, list_row)
    print(f"✅ Restored {len(lists)} lists")
    
    # Restore list items
    for item in items:
        current_cursor.execute("""
            INSERT INTO list_items 
            (id, list_id, task_text, priority, completed, completed_at, metadata, journey_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, item)
    print(f"✅ Restored {len(items)} list items")
    
    current_conn.commit()
    
    # Verify
    print("\n📋 Verification:")
    for user in REAL_USERS:
        current_cursor.execute("SELECT COUNT(*) FROM lists WHERE user_id = ?", (user,))
        count = current_cursor.fetchone()[0]
        if count > 0:
            print(f"  ✓ {user}: {count} lists")
    
    current_conn.close()
    print("\n✅ All lists and items restored!")

if __name__ == "__main__":
    main()






