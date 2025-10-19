#!/usr/bin/env python3
"""
Temporal Memory Integration for Zoe Chat Router
Integrates temporal memory capabilities with existing chat system
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import sqlite3

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TemporalMemoryIntegration:
    """Integrates temporal memory with Zoe's chat system"""
    
    def __init__(self, db_path="/app/data/memory.db"):
        self.db_path = db_path
        self.current_episodes = {}  # Track active episodes per user
        logger.info("Temporal Memory Integration initialized")
    
    async def start_conversation_episode(self, user_id: str, context_type: str = "chat") -> int:
        """Start a new conversation episode"""
        try:
            # Check if there's an active episode
            active_episode = await self._get_active_episode(user_id, context_type)
            if active_episode:
                logger.info(f"Using existing active episode {active_episode} for user {user_id}")
                return active_episode
            
            # Create new episode
            episode_id = await self._create_episode(user_id, context_type)
            self.current_episodes[f"{user_id}_{context_type}"] = episode_id
            logger.info(f"Started new episode {episode_id} for user {user_id} ({context_type})")
            return episode_id
            
        except Exception as e:
            logger.error(f"Error starting conversation episode: {e}")
            return None
    
    async def add_message_to_episode(self, user_id: str, message: str, response: str, 
                                   context_type: str = "chat", memory_fact_id: Optional[int] = None):
        """Add message and response to current episode"""
        try:
            episode_id = await self.start_conversation_episode(user_id, context_type)
            if not episode_id:
                return
            
            # Store conversation in episode
            await self._store_conversation_turn(episode_id, message, response, memory_fact_id)
            
        except Exception as e:
            logger.error(f"Error adding message to episode: {e}")
    
    async def search_with_temporal_context(self, query: str, user_id: str, 
                                        time_range: str = "all", limit: int = 10) -> Dict:
        """Search memories with temporal awareness"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Build time filter
            time_filter = self._build_time_filter(time_range)
            params = [user_id, f"%{query}%"]
            
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
                    "episode_summary": row[10],
                    "temporal_context": self._get_temporal_context(row[6])
                })
            
            conn.close()
            logger.info(f"Temporal search found {len(results)} results for '{query}' ({time_range})")
            return {
                "query": query,
                "time_range": time_range,
                "results": results,
                "total_results": len(results)
            }
            
        except Exception as e:
            logger.error(f"Error in temporal search: {e}")
            return {"error": str(e)}
    
    async def get_episode_context(self, user_id: str, context_type: str = "chat") -> Dict:
        """Get context from current and recent episodes"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get current episode
            current_episode = await self._get_active_episode(user_id, context_type)
            
            # Get recent episodes (last 3)
            cursor.execute("""
                SELECT id, start_time, end_time, summary, message_count, context_type
                FROM conversation_episodes 
                WHERE user_id = ? AND context_type = ?
                ORDER BY start_time DESC
                LIMIT 3
            """, (user_id, context_type))
            
            episodes = []
            for row in cursor.fetchall():
                episodes.append({
                    "id": row[0],
                    "start_time": row[1],
                    "end_time": row[2],
                    "summary": row[3],
                    "message_count": row[4],
                    "context_type": row[5],
                    "is_current": row[0] == current_episode
                })
            
            conn.close()
            
            return {
                "current_episode": current_episode,
                "recent_episodes": episodes,
                "episode_count": len(episodes)
            }
            
        except Exception as e:
            logger.error(f"Error getting episode context: {e}")
            return {"error": str(e)}
    
    async def close_episode(self, user_id: str, context_type: str = "chat", 
                          summary: Optional[str] = None):
        """Close current episode"""
        try:
            episode_id = await self._get_active_episode(user_id, context_type)
            if not episode_id:
                return
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Close episode
            cursor.execute("""
                UPDATE conversation_episodes 
                SET end_time = ?, summary = ?
                WHERE id = ?
            """, (datetime.now(), summary, episode_id))
            
            conn.commit()
            conn.close()
            
            # Remove from current episodes
            key = f"{user_id}_{context_type}"
            if key in self.current_episodes:
                del self.current_episodes[key]
            
            logger.info(f"Closed episode {episode_id} for user {user_id}")
            
        except Exception as e:
            logger.error(f"Error closing episode: {e}")
    
    async def _get_active_episode(self, user_id: str, context_type: str) -> Optional[int]:
        """Get active episode for user"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            timeout_minutes = 30 if context_type == "chat" else 120
            timeout_threshold = datetime.now() - timedelta(minutes=timeout_minutes)
            
            cursor.execute("""
                SELECT id FROM conversation_episodes 
                WHERE user_id = ? AND context_type = ? 
                AND end_time IS NULL 
                AND start_time > ?
                ORDER BY start_time DESC 
                LIMIT 1
            """, (user_id, context_type, timeout_threshold))
            
            row = cursor.fetchone()
            conn.close()
            
            return row[0] if row else None
            
        except Exception as e:
            logger.error(f"Error getting active episode: {e}")
            return None
    
    async def _create_episode(self, user_id: str, context_type: str) -> int:
        """Create new episode"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            timeout_minutes = 30 if context_type == "chat" else 120
            
            cursor.execute("""
                INSERT INTO conversation_episodes 
                (user_id, context_type, timeout_minutes)
                VALUES (?, ?, ?)
            """, (user_id, context_type, timeout_minutes))
            
            episode_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            return episode_id
            
        except Exception as e:
            logger.error(f"Error creating episode: {e}")
            return None
    
    async def _store_conversation_turn(self, episode_id: int, message: str, 
                                     response: str, memory_fact_id: Optional[int] = None):
        """Store conversation turn in episode"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Increment message count
            cursor.execute("""
                UPDATE conversation_episodes 
                SET message_count = message_count + 1
                WHERE id = ?
            """, (episode_id,))
            
            # If we have a memory fact ID, associate it with the episode
            if memory_fact_id:
                cursor.execute("""
                    UPDATE memory_facts 
                    SET episode_id = ?
                    WHERE id = ?
                """, (episode_id, memory_fact_id))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error storing conversation turn: {e}")
    
    def _build_time_filter(self, time_range: str) -> str:
        """Build SQL time filter based on range"""
        if time_range == "today":
            return "AND DATE(mf.created_at) = DATE('now')"
        elif time_range == "yesterday":
            return "AND DATE(mf.created_at) = DATE('now', '-1 day')"
        elif time_range == "this_week":
            return "AND mf.created_at >= DATE('now', '-7 days')"
        elif time_range == "last_week":
            return "AND mf.created_at >= DATE('now', '-14 days') AND mf.created_at < DATE('now', '-7 days')"
        elif time_range == "this_month":
            return "AND mf.created_at >= DATE('now', '-30 days')"
        else:
            return ""
    
    def _get_temporal_context(self, created_at: str) -> str:
        """Get human-readable temporal context"""
        try:
            created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            now = datetime.now()
            diff = now - created
            
            if diff.days > 0:
                return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
            elif diff.seconds > 3600:
                hours = diff.seconds // 3600
                return f"{hours} hour{'s' if hours > 1 else ''} ago"
            elif diff.seconds > 60:
                minutes = diff.seconds // 60
                return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
            else:
                return "just now"
        except:
            return "unknown time"

# Global instance for integration
temporal_memory = TemporalMemoryIntegration()

# Integration functions for chat router
async def enhance_memory_search_with_temporal(query: str, user_id: str, 
                                           time_range: str = "all") -> Dict:
    """Enhance existing memory search with temporal context"""
    try:
        # Get temporal search results
        temporal_results = await temporal_memory.search_with_temporal_context(
            query, user_id, time_range, limit=10
        )
        
        # Get episode context
        episode_context = await temporal_memory.get_episode_context(user_id)
        
        return {
            "temporal_results": temporal_results,
            "episode_context": episode_context,
            "enhanced": True
        }
        
    except Exception as e:
        logger.error(f"Error enhancing memory search: {e}")
        return {"enhanced": False, "error": str(e)}

async def start_chat_episode(user_id: str, context_type: str = "chat") -> int:
    """Start chat episode for user"""
    return await temporal_memory.start_conversation_episode(user_id, context_type)

async def add_chat_turn(user_id: str, message: str, response: str, 
                       context_type: str = "chat", memory_fact_id: Optional[int] = None):
    """Add chat turn to current episode"""
    await temporal_memory.add_message_to_episode(
        user_id, message, response, context_type, memory_fact_id
    )

async def close_chat_episode(user_id: str, context_type: str = "chat", 
                           summary: Optional[str] = None):
    """Close current chat episode"""
    await temporal_memory.close_episode(user_id, context_type, summary)

# Test the integration
async def test_temporal_integration():
    """Test temporal memory integration"""
    logger.info("🧪 Testing Temporal Memory Integration...")
    
    try:
        # Test episode creation
        episode_id = await start_chat_episode("test_user", "chat")
        logger.info(f"✅ Started episode {episode_id}")
        
        # Test adding chat turn
        await add_chat_turn("test_user", "Hello", "Hi there!", "chat")
        logger.info("✅ Added chat turn")
        
        # Test temporal search
        results = await enhance_memory_search_with_temporal("test", "test_user", "all")
        logger.info(f"✅ Temporal search returned {len(results.get('temporal_results', {}).get('results', []))} results")
        
        # Test episode context
        context = await temporal_memory.get_episode_context("test_user", "chat")
        logger.info(f"✅ Retrieved episode context with {context.get('episode_count', 0)} episodes")
        
        # Test closing episode
        await close_chat_episode("test_user", "chat", "Test conversation")
        logger.info("✅ Closed episode")
        
        logger.info("🎉 Temporal Memory Integration tests passed!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Temporal Memory Integration tests failed: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(test_temporal_integration())
