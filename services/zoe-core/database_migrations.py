"""
Database Migrations for Zoe Core
================================

Ensures all required tables exist with proper schemas.
"""

import sqlite3
import logging

logger = logging.getLogger(__name__)

DB_PATH = "/app/data/zoe.db"

def init_developer_sessions_table():
    """Initialize developer_sessions table for session tracking"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if table exists
        cursor.execute('''
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='developer_sessions'
        ''')
        table_exists = cursor.fetchone() is not None
        
        if table_exists:
            # Check if table has correct schema
            cursor.execute("PRAGMA table_info(developer_sessions)")
            columns = {row[1] for row in cursor.fetchall()}
            
            # If updated_at column is missing, recreate table
            if 'updated_at' not in columns:
                logger.info("Recreating developer_sessions table with correct schema...")
                cursor.execute("DROP TABLE IF EXISTS developer_sessions")
                table_exists = False
        
        if not table_exists:
            cursor.execute('''
                CREATE TABLE developer_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT UNIQUE,
                    current_task TEXT,
                    last_command TEXT,
                    files_changed TEXT,
                    next_steps TEXT,
                    context_data TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create index for faster lookups
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_developer_sessions_user 
                ON developer_sessions(user_id, updated_at DESC)
            ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ developer_sessions table initialized")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize developer_sessions table: {e}")
        return False

def run_all_migrations():
    """Run all database migrations"""
    migrations = [
        ("developer_sessions", init_developer_sessions_table),
    ]
    
    results = []
    for name, migration_func in migrations:
        try:
            success = migration_func()
            results.append((name, success))
            if success:
                logger.info(f"✅ Migration '{name}' completed successfully")
            else:
                logger.error(f"❌ Migration '{name}' failed")
        except Exception as e:
            logger.error(f"❌ Migration '{name}' threw exception: {e}")
            results.append((name, False))
    
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Running database migrations...")
    results = run_all_migrations()
    print(f"\n{'='*60}")
    print(f"Migration Results:")
    print(f"{'='*60}")
    for name, success in results:
        status = "✅ SUCCESS" if success else "❌ FAILED"
        print(f"{name:30} {status}")
    print(f"{'='*60}\n")

