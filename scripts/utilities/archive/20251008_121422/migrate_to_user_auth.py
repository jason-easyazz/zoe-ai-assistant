#!/usr/bin/env python3
"""
Migrate all 'default' user data to the actual authenticated user
and update all endpoints to use session-based authentication
"""
import sqlite3
import sys

# Your actual user ID from the session
TARGET_USER_ID = "72038d8e-a3bb-4e41-9d9b-163b5736d2ce"
DB_PATH = "/app/data/zoe.db"

def migrate_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("=" * 60)
    print("MIGRATING DATA FROM 'default' TO AUTHENTICATED USER")
    print("=" * 60)
    
    # Check current state
    cursor.execute("SELECT COUNT(*) FROM lists WHERE user_id = 'default'")
    default_count = cursor.fetchone()[0]
    
    cursor.execute(f"SELECT COUNT(*) FROM lists WHERE user_id = '{TARGET_USER_ID}'")
    user_count = cursor.fetchone()[0]
    
    print(f"\n📊 Current State:")
    print(f"  - 'default' user: {default_count} lists")
    print(f"  - Your user: {user_count} lists")
    
    if default_count == 0:
        print("\n✅ No data to migrate - all clean!")
        return
    
    # Show what will be migrated
    print(f"\n📋 Lists to migrate:")
    cursor.execute("""
        SELECT id, name, list_type, list_category 
        FROM lists 
        WHERE user_id = 'default'
    """)
    for row in cursor.fetchall():
        print(f"  - ID {row[0]}: {row[1]} ({row[2]}, {row[3]})")
    
    # Perform migration
    print(f"\n🔄 Migrating to user: {TARGET_USER_ID}...")
    
    cursor.execute("""
        UPDATE lists 
        SET user_id = ? 
        WHERE user_id = 'default'
    """, (TARGET_USER_ID,))
    
    migrated = cursor.rowcount
    conn.commit()
    
    print(f"✅ Migrated {migrated} lists")
    
    # Verify
    cursor.execute("SELECT COUNT(*) FROM lists WHERE user_id = 'default'")
    remaining = cursor.fetchone()[0]
    
    cursor.execute(f"SELECT COUNT(*) FROM lists WHERE user_id = '{TARGET_USER_ID}'")
    new_total = cursor.fetchone()[0]
    
    print(f"\n📊 After Migration:")
    print(f"  - 'default' user: {remaining} lists")
    print(f"  - Your user: {new_total} lists")
    
    # Check for other tables that might have default user
    print(f"\n🔍 Checking other tables...")
    
    # Check if there are other tables with user_id
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' 
        AND sql LIKE '%user_id%'
    """)
    
    tables_with_user = cursor.fetchall()
    for (table_name,) in tables_with_user:
        if table_name == 'lists':
            continue
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE user_id = 'default'")
            count = cursor.fetchone()[0]
            if count > 0:
                print(f"  ⚠️  {table_name}: {count} rows with 'default' user")
                # Migrate those too
                cursor.execute(f"""
                    UPDATE {table_name} 
                    SET user_id = ? 
                    WHERE user_id = 'default'
                """, (TARGET_USER_ID,))
                print(f"  ✅ Migrated {cursor.rowcount} rows from {table_name}")
        except Exception as e:
            print(f"  ℹ️  {table_name}: {e}")
    
    conn.commit()
    conn.close()
    
    print(f"\n" + "=" * 60)
    print("✅ MIGRATION COMPLETE!")
    print("=" * 60)

if __name__ == "__main__":
    migrate_data()

