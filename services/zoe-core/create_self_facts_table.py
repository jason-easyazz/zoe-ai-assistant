#!/usr/bin/env python3
"""Create self_facts table for memory storage"""

import sqlite3
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_self_facts_table():
    """Create the self_facts table if it doesn't exist"""
    db_path = "/app/data/zoe.db"
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create self_facts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS self_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                source TEXT DEFAULT 'user_stated',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, fact_key)
            )
        """)
        
        # Create index for faster searches
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_self_facts_user_key 
            ON self_facts(user_id, fact_key)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_self_facts_search 
            ON self_facts(user_id, fact_value)
        """)
        
        conn.commit()
        conn.close()
        
        logger.info("✅ self_facts table created successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to create self_facts table: {e}")
        return False

if __name__ == "__main__":
    create_self_facts_table()

