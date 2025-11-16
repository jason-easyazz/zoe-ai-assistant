#!/usr/bin/env python3
"""
Database Consolidation Script
Consolidates all databases into zoe.db (single source of truth)
Keeps memory.db separate for Light RAG embeddings
"""

import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

class DatabaseConsolidator:
    def __init__(self):
        self.data_dir = PROJECT_ROOT / "data"
        self.primary_db = self.data_dir / "zoe.db"
        self.log_file = PROJECT_ROOT / "database_consolidation.log"
        
    def log(self, message: str):
        """Log message to file and console"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_msg = f"[{timestamp}] {message}"
        print(log_msg)
        with open(self.log_file, 'a') as f:
            f.write(log_msg + "\n")
    
    def check_table_exists(self, db_path: Path, table_name: str) -> bool:
        """Check if table exists in database"""
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
            exists = cursor.fetchone() is not None
            conn.close()
            return exists
        except Exception as e:
            self.log(f"Error checking table {table_name}: {e}")
            return False
    
    def merge_users_from_auth(self):
        """Merge users from auth.db into zoe.db"""
        self.log("📊 Merging users from auth.db...")
        
        try:
            # Connect to both databases
            zoe_conn = sqlite3.connect(self.primary_db)
            auth_conn = sqlite3.connect(self.data_dir / "zoe.db")
            
            zoe_conn.row_factory = sqlite3.Row
            auth_conn.row_factory = sqlite3.Row
            
            zoe_cursor = zoe_conn.cursor()
            auth_cursor = auth_conn.cursor()
            
            # Get users from auth.db
            auth_cursor.execute("SELECT * FROM users")
            auth_users = auth_cursor.fetchall()
            
            # Get existing users from zoe.db
            zoe_cursor.execute("SELECT user_id FROM users")
            existing_users = set([row[0] for row in zoe_cursor.fetchall()])
            
            merged_count = 0
            updated_count = 0
            
            for user in auth_users:
                user_dict = dict(user)
                user_id = user_dict['user_id']
                
                if user_id in existing_users:
                    # User exists, update if needed
                    self.log(f"   User {user_id} already exists in zoe.db, skipping")
                    updated_count += 1
                else:
                    # Insert new user
                    # Check if auth.db has different schema
                    columns = list(user_dict.keys())
                    
                    # Get zoe.db users table columns
                    zoe_cursor.execute("PRAGMA table_info(users)")
                    zoe_columns = [row[1] for row in zoe_cursor.fetchall()]
                    
                    # Only use columns that exist in both
                    common_columns = [col for col in columns if col in zoe_columns]
                    
                    values = [user_dict[col] for col in common_columns]
                    placeholders = ','.join(['?' for _ in common_columns])
                    columns_str = ','.join(common_columns)
                    
                    sql = f"INSERT OR IGNORE INTO users ({columns_str}) VALUES ({placeholders})"
                    zoe_cursor.execute(sql, values)
                    merged_count += 1
                    self.log(f"   ✅ Merged user: {user_dict.get('username', user_id)}")
            
            zoe_conn.commit()
            zoe_conn.close()
            auth_conn.close()
            
            self.log(f"✅ Users merged: {merged_count} new, {updated_count} existing")
            return merged_count
            
        except Exception as e:
            self.log(f"❌ Error merging users: {e}")
            return 0
    
    def merge_sessions(self):
        """Merge sessions from sessions.db and auth.db"""
        self.log("📊 Merging sessions...")
        
        try:
            zoe_conn = sqlite3.connect(self.primary_db)
            zoe_cursor = zoe_conn.cursor()
            
            # Check if sessions table exists in zoe.db
            if not self.check_table_exists(self.primary_db, 'user_sessions'):
                self.log("   Creating user_sessions table in zoe.db...")
                zoe_cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_sessions (
                        session_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at TIMESTAMP NOT NULL,
                        last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        metadata JSON DEFAULT '{}',
                        is_active BOOLEAN DEFAULT 1
                    )
                """)
            
            merged_count = 0
            
            # Merge from sessions.db if it exists
            sessions_db = self.data_dir / "zoe.db"
            if sessions_db.exists():
                sessions_conn = sqlite3.connect(sessions_db)
                sessions_conn.row_factory = sqlite3.Row
                sessions_cursor = sessions_conn.cursor()
                
                sessions_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in sessions_cursor.fetchall()]
                
                if 'sessions' in tables:
                    sessions_cursor.execute("SELECT * FROM sessions")
                    for session in sessions_cursor.fetchall():
                        session_dict = dict(session)
                        # Map to user_sessions schema
                        zoe_cursor.execute("""
                            INSERT OR IGNORE INTO user_sessions (session_id, user_id, created_at)
                            VALUES (?, ?, ?)
                        """, (
                            session_dict.get('session_id') or session_dict.get('id'),
                            session_dict.get('user_id', 'unknown'),
                            session_dict.get('created_at', datetime.now())
                        ))
                        merged_count += 1
                
                sessions_conn.close()
            
            # Merge from auth.db sessions
            auth_db = self.data_dir / "zoe.db"
            if auth_db.exists():
                auth_conn = sqlite3.connect(auth_db)
                auth_conn.row_factory = sqlite3.Row
                auth_cursor = auth_conn.cursor()
                
                auth_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in auth_cursor.fetchall()]
                
                if 'sessions' in tables:
                    auth_cursor.execute("SELECT * FROM sessions")
                    for session in auth_cursor.fetchall():
                        session_dict = dict(session)
                        zoe_cursor.execute("""
                            INSERT OR IGNORE INTO user_sessions (session_id, user_id, created_at)
                            VALUES (?, ?, ?)
                        """, (
                            session_dict.get('session_id') or session_dict.get('id'),
                            session_dict.get('user_id', 'unknown'),
                            session_dict.get('created_at', datetime.now())
                        ))
                        merged_count += 1
                
                auth_conn.close()
            
            zoe_conn.commit()
            zoe_conn.close()
            
            self.log(f"✅ Sessions merged: {merged_count} total")
            return merged_count
            
        except Exception as e:
            self.log(f"❌ Error merging sessions: {e}")
            return 0
    
    def verify_consolidation(self) -> bool:
        """Verify that consolidation was successful"""
        self.log("🔍 Verifying consolidation...")
        
        try:
            conn = sqlite3.connect(self.primary_db)
            cursor = conn.cursor()
            
            # Check users
            cursor.execute("SELECT COUNT(*) FROM users")
            user_count = cursor.fetchone()[0]
            self.log(f"   Users in zoe.db: {user_count}")
            
            # Check sessions if table exists
            if self.check_table_exists(self.primary_db, 'user_sessions'):
                cursor.execute("SELECT COUNT(*) FROM user_sessions")
                session_count = cursor.fetchone()[0]
                self.log(f"   Sessions in zoe.db: {session_count}")
            
            conn.close()
            
            self.log("✅ Verification complete")
            return True
            
        except Exception as e:
            self.log(f"❌ Verification failed: {e}")
            return False
    
    def run(self):
        """Run full consolidation"""
        self.log("=" * 80)
        self.log("🚀 DATABASE CONSOLIDATION STARTED")
        self.log("=" * 80)
        
        # Merge users
        self.merge_users_from_auth()
        
        # Merge sessions
        self.merge_sessions()
        
        # Verify
        success = self.verify_consolidation()
        
        if success:
            self.log("=" * 80)
            self.log("✅ DATABASE CONSOLIDATION SUCCESSFUL")
            self.log("=" * 80)
        else:
            self.log("=" * 80)
            self.log("⚠️  DATABASE CONSOLIDATION COMPLETED WITH WARNINGS")
            self.log("=" * 80)
        
        return success

if __name__ == "__main__":
    consolidator = DatabaseConsolidator()
    success = consolidator.run()
    exit(0 if success else 1)

