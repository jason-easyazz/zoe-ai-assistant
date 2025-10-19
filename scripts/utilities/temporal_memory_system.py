#!/usr/bin/env python3
"""
Temporal Memory System Implementation
Extends Light RAG with episode management and time-based queries
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class Episode:
    id: int
    user_id: str
    session_id: Optional[str]
    start_time: datetime
    end_time: Optional[datetime]
    summary: Optional[str]
    context_type: str
    timeout_minutes: int
    message_count: int
    created_at: datetime

class TemporalMemoryExtension:
    """Extends Light RAG with temporal capabilities"""
    
    def __init__(self, db_path="/app/data/memory.db"):
        self.db_path = db_path
        self.init_temporal_schema()
        logger.info("Temporal Memory Extension initialized")
    
    def init_temporal_schema(self):
        """Initialize temporal memory database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Add episode_id to existing memory_facts table
            cursor.execute("""
                ALTER TABLE memory_facts 
                ADD COLUMN episode_id INTEGER DEFAULT NULL
            """)
            logger.info("Added episode_id column to memory_facts table")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                logger.info("episode_id column already exists")
            else:
                logger.error(f"Error adding episode_id column: {e}")
        
        try:
            # Add user_id to existing memory_facts table for user isolation
            cursor.execute("""
                ALTER TABLE memory_facts 
                ADD COLUMN user_id TEXT DEFAULT 'default'
            """)
            logger.info("Added user_id column to memory_facts table")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                logger.info("user_id column already exists")
            else:
                logger.error(f"Error adding user_id column: {e}")
        
        # Create conversation episodes table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                session_id TEXT,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                summary TEXT,
                context_type TEXT DEFAULT 'chat',
                timeout_minutes INTEGER DEFAULT 30,
                message_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create temporal search indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_user_time 
            ON conversation_episodes(user_id, start_time)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_facts_episode 
            ON memory_facts(episode_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_context_type 
            ON conversation_episodes(context_type, start_time)
        """)
        
        conn.commit()
        conn.close()
        logger.info("Temporal memory schema initialized successfully")
    
    def create_episode(self, user_id: str, context_type: str = "chat", 
                      session_id: Optional[str] = None) -> int:
        """Create new episode with context-aware timeout"""
        timeout_minutes = 30 if context_type == "chat" else 120
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO conversation_episodes 
                (user_id, session_id, context_type, timeout_minutes)
                VALUES (?, ?, ?, ?)
            """, (user_id, session_id, context_type, timeout_minutes))
            
            episode_id = cursor.lastrowid
            conn.commit()
            
            logger.info(f"Created episode {episode_id} for user {user_id} ({context_type})")
            return episode_id
            
        except Exception as e:
            logger.error(f"Error creating episode: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def get_active_episode(self, user_id: str, context_type: str = "chat") -> Optional[int]:
        """Get active episode for user, create new one if needed"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Look for active episode (not ended, within timeout)
            timeout_minutes = 30 if context_type == "chat" else 120
            timeout_threshold = datetime.now() - timedelta(minutes=timeout_minutes)
            
            cursor.execute("""
                SELECT id, start_time FROM conversation_episodes 
                WHERE user_id = ? AND context_type = ? 
                AND end_time IS NULL 
                AND start_time > ?
                ORDER BY start_time DESC 
                LIMIT 1
            """, (user_id, context_type, timeout_threshold))
            
            row = cursor.fetchone()
            if row:
                episode_id, start_time = row
                logger.info(f"Found active episode {episode_id} for user {user_id}")
                return episode_id
            
            # No active episode found, create new one
            episode_id = self.create_episode(user_id, context_type)
            logger.info(f"Created new episode {episode_id} for user {user_id}")
            return episode_id
            
        except Exception as e:
            logger.error(f"Error getting active episode: {e}")
            return None
        finally:
            conn.close()
    
    def add_message_to_episode(self, episode_id: int, memory_fact_id: int):
        """Associate a memory fact with an episode"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Update memory_facts table
            cursor.execute("""
                UPDATE memory_facts 
                SET episode_id = ?
                WHERE id = ?
            """, (episode_id, memory_fact_id))
            
            # Increment message count
            cursor.execute("""
                UPDATE conversation_episodes 
                SET message_count = message_count + 1
                WHERE id = ?
            """, (episode_id,))
            
            conn.commit()
            logger.debug(f"Added memory fact {memory_fact_id} to episode {episode_id}")
            
        except Exception as e:
            logger.error(f"Error adding message to episode: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def close_episode(self, episode_id: int, summary: Optional[str] = None):
        """Close an episode and optionally generate summary"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE conversation_episodes 
                SET end_time = ?, summary = ?
                WHERE id = ?
            """, (datetime.now(), summary, episode_id))
            
            conn.commit()
            logger.info(f"Closed episode {episode_id}")
            
        except Exception as e:
            logger.error(f"Error closing episode: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def temporal_search(self, query: str, user_id: str, time_range: str = "all", 
                      limit: int = 10) -> List[Dict]:
        """Search memories with temporal awareness"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Build time filter based on range
            time_filter = ""
            params = [user_id, f"%{query}%"]
            
            if time_range == "today":
                time_filter = "AND DATE(mf.created_at) = DATE('now')"
            elif time_range == "yesterday":
                time_filter = "AND DATE(mf.created_at) = DATE('now', '-1 day')"
            elif time_range == "this_week":
                time_filter = "AND mf.created_at >= DATE('now', '-7 days')"
            elif time_range == "last_week":
                time_filter = "AND mf.created_at >= DATE('now', '-14 days') AND mf.created_at < DATE('now', '-7 days')"
            elif time_range == "this_month":
                time_filter = "AND mf.created_at >= DATE('now', '-30 days')"
            
            # Search with temporal context
            cursor.execute(f"""
                SELECT 
                    mf.id,
                    mf.fact,
                    mf.entity_type,
                    mf.entity_id,
                    mf.category,
                    mf.importance,
                    mf.created_at,
                    ce.id as episode_id,
                    ce.start_time as episode_start,
                    ce.context_type,
                    ce.summary as episode_summary
                FROM memory_facts mf
                LEFT JOIN conversation_episodes ce ON mf.episode_id = ce.id
                WHERE mf.user_id = ? AND mf.fact LIKE ? {time_filter}
                ORDER BY mf.created_at DESC, mf.importance DESC
                LIMIT ?
            """, params + [limit])
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "fact_id": row[0],
                    "fact": row[1],
                    "entity_type": row[2],
                    "entity_id": row[3],
                    "category": row[4],
                    "importance": row[5],
                    "created_at": row[6],
                    "episode_id": row[7],
                    "episode_start": row[8],
                    "context_type": row[9],
                    "episode_summary": row[10]
                })
            
            logger.info(f"Temporal search found {len(results)} results for '{query}' ({time_range})")
            return results
            
        except Exception as e:
            logger.error(f"Error in temporal search: {e}")
            return []
        finally:
            conn.close()
    
    def get_episode_summary(self, episode_id: int) -> Optional[str]:
        """Get or generate episode summary"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Get episode details
            cursor.execute("""
                SELECT summary, message_count, start_time, end_time
                FROM conversation_episodes 
                WHERE id = ?
            """, (episode_id,))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            summary, message_count, start_time, end_time = row
            
            # Return existing summary if available
            if summary:
                return summary
            
            # Generate summary if not available and episode is closed
            if end_time and message_count > 0:
                # Get facts from this episode
                cursor.execute("""
                    SELECT fact FROM memory_facts 
                    WHERE episode_id = ? 
                    ORDER BY importance DESC, created_at ASC
                    LIMIT 10
                """, (episode_id,))
                
                facts = [row[0] for row in cursor.fetchall()]
                if facts:
                    # Simple summary generation (in production, use LLM)
                    summary = f"Episode with {message_count} messages covering: " + ", ".join(facts[:3])
                    if len(facts) > 3:
                        summary += f" and {len(facts) - 3} more topics"
                    
                    # Update episode with summary
                    cursor.execute("""
                        UPDATE conversation_episodes 
                        SET summary = ?
                        WHERE id = ?
                    """, (summary, episode_id))
                    
                    conn.commit()
                    logger.info(f"Generated summary for episode {episode_id}")
                    return summary
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting episode summary: {e}")
            return None
        finally:
            conn.close()
    
    def get_episode_history(self, user_id: str, limit: int = 20) -> List[Dict]:
        """Get episode history for user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT 
                    id, session_id, start_time, end_time, summary,
                    context_type, message_count, created_at
                FROM conversation_episodes 
                WHERE user_id = ?
                ORDER BY start_time DESC
                LIMIT ?
            """, (user_id, limit))
            
            episodes = []
            for row in cursor.fetchall():
                episodes.append({
                    "id": row[0],
                    "session_id": row[1],
                    "start_time": row[2],
                    "end_time": row[3],
                    "summary": row[4],
                    "context_type": row[5],
                    "message_count": row[6],
                    "created_at": row[7]
                })
            
            logger.info(f"Retrieved {len(episodes)} episodes for user {user_id}")
            return episodes
            
        except Exception as e:
            logger.error(f"Error getting episode history: {e}")
            return []
        finally:
            conn.close()
    
    def cleanup_old_episodes(self, days_to_keep: int = 30):
        """Clean up old episodes to prevent database bloat"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cutoff_date = datetime.now() - timedelta(days=days_to_keep)
            
            # Count episodes to be deleted
            cursor.execute("""
                SELECT COUNT(*) FROM conversation_episodes 
                WHERE start_time < ?
            """, (cutoff_date,))
            
            count = cursor.fetchone()[0]
            
            if count > 0:
                # Delete old episodes
                cursor.execute("""
                    DELETE FROM conversation_episodes 
                    WHERE start_time < ?
                """, (cutoff_date,))
                
                conn.commit()
                logger.info(f"Cleaned up {count} old episodes")
            else:
                logger.info("No old episodes to clean up")
                
        except Exception as e:
            logger.error(f"Error cleaning up old episodes: {e}")
            conn.rollback()
        finally:
            conn.close()

# Integration with existing Light RAG system
class TemporalLightRAGIntegration:
    """Integrates temporal memory with existing Light RAG system"""
    
    def __init__(self, light_rag_system, temporal_extension):
        self.light_rag = light_rag_system
        self.temporal = temporal_extension
        logger.info("Temporal Light RAG integration initialized")
    
    def add_memory_with_episode(self, entity_type: str, entity_id: int, 
                               fact: str, user_id: str, context_type: str = "chat",
                               category: str = "general", importance: int = 5) -> Dict:
        """Add memory fact with automatic episode association"""
        try:
            # Get or create active episode
            episode_id = self.temporal.get_active_episode(user_id, context_type)
            if not episode_id:
                episode_id = self.temporal.create_episode(user_id, context_type)
            
            # Add memory using existing Light RAG system
            result = self.light_rag.add_memory_with_embedding(
                entity_type, entity_id, fact, category, importance
            )
            
            # Associate with episode
            if result.get("fact_id"):
                self.temporal.add_message_to_episode(episode_id, result["fact_id"])
                result["episode_id"] = episode_id
            
            logger.info(f"Added memory with episode association: episode {episode_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error adding memory with episode: {e}")
            raise
    
    def search_with_temporal_context(self, query: str, user_id: str, 
                                   time_range: str = "all", limit: int = 10) -> Dict:
        """Search with both semantic and temporal context"""
        try:
            # Get semantic results from Light RAG
            semantic_results = self.light_rag.light_rag_search(query, limit)
            
            # Get temporal results
            temporal_results = self.temporal.temporal_search(query, user_id, time_range, limit)
            
            # Combine and deduplicate results
            combined_results = self._combine_search_results(semantic_results, temporal_results)
            
            return {
                "query": query,
                "time_range": time_range,
                "semantic_results": len(semantic_results),
                "temporal_results": len(temporal_results),
                "combined_results": combined_results,
                "total_results": len(combined_results)
            }
            
        except Exception as e:
            logger.error(f"Error in temporal context search: {e}")
            return {"error": str(e)}
    
    def _combine_search_results(self, semantic_results, temporal_results):
        """Combine and deduplicate search results"""
        # Simple deduplication by fact_id
        seen_ids = set()
        combined = []
        
        # Add semantic results first (higher priority)
        for result in semantic_results:
            if hasattr(result, 'fact_id'):
                fact_id = result.fact_id
            else:
                fact_id = result.get('fact_id', result.get('id'))
            
            if fact_id not in seen_ids:
                combined.append(result)
                seen_ids.add(fact_id)
        
        # Add temporal results that aren't already included
        for result in temporal_results:
            fact_id = result.get('fact_id')
            if fact_id not in seen_ids:
                combined.append(result)
                seen_ids.add(fact_id)
        
        return combined

# Test the temporal memory system
def test_temporal_memory():
    """Test the temporal memory system"""
    logger.info("🧪 Testing Temporal Memory System...")
    
    try:
        # Initialize temporal extension
        temporal = TemporalMemoryExtension()
        
        # Test episode creation
        episode_id = temporal.create_episode("test_user", "chat")
        logger.info(f"✅ Created episode {episode_id}")
        
        # Test temporal search
        results = temporal.temporal_search("test query", "test_user", "all", 5)
        logger.info(f"✅ Temporal search returned {len(results)} results")
        
        # Test episode history
        history = temporal.get_episode_history("test_user", 10)
        logger.info(f"✅ Retrieved {len(history)} episodes from history")
        
        # Test cleanup
        temporal.cleanup_old_episodes(30)
        logger.info("✅ Cleanup completed")
        
        logger.info("🎉 Temporal Memory System tests passed!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Temporal Memory System tests failed: {e}")
        return False

if __name__ == "__main__":
    test_temporal_memory()
