#!/usr/bin/env python3
"""Restore admin user after database reset"""
import sqlite3
import hashlib

DB_PATH = "/app/data/zoe.db"

def main():
    print("🔧 Restoring admin user...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create auth_users table
    cursor.execute("""
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
    print("✅ auth_users table created")
    
    # Check if jason exists
    cursor.execute("SELECT user_id FROM auth_users WHERE user_id = 'jason'")
    if cursor.fetchone():
        print("✅ User 'jason' already exists")
    else:
        # Create admin user
        password = "admin123"
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        cursor.execute("""
            INSERT INTO auth_users (user_id, username, email, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
        """, ('jason', 'jason', 'jason@zoe.local', password_hash, 'admin'))
        
        print(f"✅ Created admin user 'jason'")
        print(f"   Temporary password: {password}")
        print("   ⚠️  CHANGE THIS PASSWORD!")
    
    conn.commit()
    
    # Verify
    print("\n📋 Users in database:")
    cursor.execute("SELECT user_id, username, role FROM auth_users")
    for row in cursor.fetchall():
        print(f"  - {row[0]} ({row[1]}) - {row[2]}")
    
    conn.close()
    print("\n✅ Done!")

if __name__ == "__main__":
    main()






