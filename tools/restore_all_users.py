#!/usr/bin/env python3
"""Restore all users from backup with original passwords"""
import sqlite3

BACKUP_DB = "/home/zoe/assistant/backups/pre_lists_enhancement_20251122_183136/zoe.db"
CURRENT_DB = "/app/data/zoe.db"

def main():
    # Read from backup
    print("📖 Reading users from backup...")
    backup_conn = sqlite3.connect(BACKUP_DB)
    backup_cursor = backup_conn.cursor()
    
    backup_cursor.execute("""
        SELECT user_id, username, email, password_hash, role, is_admin 
        FROM users 
        WHERE user_id != 'system'
    """)
    users = backup_cursor.fetchall()
    backup_conn.close()
    
    print(f"✅ Found {len(users)} users in backup")
    
    # Write to current database
    print("\n💾 Restoring to current database...")
    current_conn = sqlite3.connect(CURRENT_DB)
    current_cursor = current_conn.cursor()
    
    # Create auth_users table
    current_cursor.execute("""
        CREATE TABLE IF NOT EXISTS auth_users (
            user_id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            password_hash TEXT,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    """)
    
    # Clear existing
    current_cursor.execute("DELETE FROM auth_users")
    
    # Insert each user
    for user in users:
        user_id, username, email, password_hash, role, is_admin = user
        
        # Convert is_admin to role
        if is_admin == 1:
            role = 'admin'
        
        current_cursor.execute("""
            INSERT INTO auth_users (user_id, username, email, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, username, email, password_hash, role))
        
        print(f"✅ {username} ({user_id}) - {role}")
    
    current_conn.commit()
    
    # Verify
    print("\n📋 Verification:")
    current_cursor.execute("SELECT user_id, username, email, role FROM auth_users")
    for row in current_cursor.fetchall():
        print(f"  ✓ {row[1]} ({row[0]}) - {row[3]} <{row[2]}>")
    
    current_conn.close()
    print("\n✅ All users restored with original passwords!")

if __name__ == "__main__":
    main()






