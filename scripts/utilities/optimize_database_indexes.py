#!/usr/bin/env python3
"""
Database Index Optimization Script
Creates indexes on frequently queried columns to improve memory search performance
"""

import sqlite3
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def optimize_zoe_db():
    """Add indexes to zoe.db for better performance"""
    db_path = "/app/data/zoe.db"
    
    if not Path(db_path).exists():
        logger.warning(f"Database not found at {db_path}, skipping optimization")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Indexes for people table
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_user_name ON people(user_id, name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_user_interaction ON people(user_id, last_interaction)")
        logger.info("✅ Created people table indexes")
        
        # Indexes for projects table
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user_status ON projects(user_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user_name ON projects(user_id, name)")
        logger.info("✅ Created projects table indexes")
        
        # Indexes for notes table
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_user_created ON notes(user_id, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_user_title ON notes(user_id, title)")
        logger.info("✅ Created notes table indexes")
        
        # Indexes for events table
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_user_date ON events(user_id, start_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_user_created ON events(user_id, created_at)")
        logger.info("✅ Created events table indexes")
        
        # Indexes for lists table
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lists_user_type ON lists(user_id, type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lists_user_name ON lists(user_id, name)")
        logger.info("✅ Created lists table indexes")
        
        # Indexes for journal_entries table
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_user_created ON journal_entries(user_id, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_user_mood ON journal_entries(user_id, mood)")
        logger.info("✅ Created journal_entries table indexes")
        
        # Indexes for user_feedback table (satisfaction tracking)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user_timestamp ON user_feedback(user_id, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user_type ON user_feedback(user_id, feedback_type)")
        logger.info("✅ Created user_feedback table indexes")
        
        # Indexes for interaction_tracking table
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_interaction_user_timestamp ON interaction_tracking(user_id, timestamp)")
        logger.info("✅ Created interaction_tracking table indexes")
        
        conn.commit()
        conn.close()
        
        logger.info("✅ zoe.db optimization complete")
        
    except Exception as e:
        logger.error(f"Failed to optimize zoe.db: {e}")

def optimize_memory_db():
    """Add indexes to memory.db for better temporal memory performance"""
    db_path = "/app/data/memory.db"
    
    if not Path(db_path).exists():
        logger.warning(f"Database not found at {db_path}, skipping optimization")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Indexes for memory_facts table
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_facts_user ON memory_facts(user_id, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_facts_user_entity ON memory_facts(user_id, entity_type, entity_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_facts_user_category ON memory_facts(user_id, category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_facts_episode ON memory_facts(episode_id)")
        logger.info("✅ Created memory_facts table indexes")
        
        # Indexes for conversation_episodes table
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_user_context ON conversation_episodes(user_id, context_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_user_time ON conversation_episodes(user_id, start_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_active ON conversation_episodes(user_id, end_time)")
        logger.info("✅ Created conversation_episodes table indexes")
        
        conn.commit()
        conn.close()
        
        logger.info("✅ memory.db optimization complete")
        
    except Exception as e:
        logger.error(f"Failed to optimize memory.db: {e}")

def analyze_query_performance():
    """Analyze query plans to verify index usage"""
    db_path = "/app/data/zoe.db"
    
    if not Path(db_path).exists():
        logger.warning(f"Database not found at {db_path}")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Sample queries to analyze
        test_queries = [
            "SELECT name, relationship FROM people WHERE user_id = 'test' AND name LIKE '%test%'",
            "SELECT name, status FROM projects WHERE user_id = 'test' AND status != 'completed'",
            "SELECT title, start_date FROM events WHERE user_id = 'test' AND start_date >= date('now')",
        ]
        
        logger.info("\n📊 Query Performance Analysis:")
        for query in test_queries:
            cursor.execute(f"EXPLAIN QUERY PLAN {query}")
            plan = cursor.fetchall()
            logger.info(f"\nQuery: {query[:60]}...")
            for row in plan:
                detail = row[-1]  # Last column has the plan detail
                if "USING INDEX" in detail:
                    logger.info(f"  ✅ {detail}")
                else:
                    logger.info(f"  ⚠️ {detail}")
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Failed to analyze queries: {e}")

if __name__ == "__main__":
    logger.info("🚀 Starting database optimization...")
    
    optimize_zoe_db()
    optimize_memory_db()
    analyze_query_performance()
    
    logger.info("\n✅ Database optimization complete!")
    logger.info("Memory search queries should now be significantly faster.")



