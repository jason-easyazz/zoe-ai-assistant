#!/usr/bin/env python3
"""
Temporal Memory Integration for Zoe Chat Router
Integrates temporal memory capabilities with existing chat system (core-local module)
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import sqlite3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TemporalMemoryIntegration:
    def __init__(self, db_path: str = "/app/data/memory.db") -> None:
        self.db_path = db_path
        self.current_episodes: Dict[str, int] = {}
        logger.info("Temporal Memory Integration initialized (core-local)")
    
    async def start_conversation_episode(self, user_id: str, context_type: str = "chat") -> Optional[int]:
        episode_id = await self._get_active_episode(user_id, context_type)
        if episode_id:
            return episode_id
        return await self._create_episode(user_id, context_type)
    
    async def add_message_to_episode(self, user_id: str, message: str, response: str,
                                     context_type: str = "chat", memory_fact_id: Optional[int] = None) -> None:
        episode_id = await self.start_conversation_episode(user_id, context_type)
        if not episode_id:
            return
        await self._store_conversation_turn(episode_id, message, response, memory_fact_id)
    
    async def search_with_temporal_context(self, query: str, user_id: str,
                                           time_range: str = "all", limit: int = 10) -> Dict:
        # Simplified: memory_facts table doesn't exist in this integration
        # Just return empty results - episode context is provided separately
        logger.info(f"Temporal search called for '{query}' (simplified integration)")
        return {"query": query, "time_range": time_range, "results": [], "total_results": 0}
    
    async def get_episode_context(self, user_id: str, context_type: str = "chat") -> Dict:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            current_episode = await self._get_active_episode(user_id, context_type)
            cursor.execute("""
                SELECT id, start_time, end_time, summary, message_count, context_type
                FROM conversation_episodes 
                WHERE user_id = ? AND context_type = ?
                ORDER BY start_time DESC
                LIMIT 3
            """, (user_id, context_type))
            episodes: List[Dict] = []
            for row in cursor.fetchall():
                episode_id = row[0]
                
                # Get recent conversation turns for this episode
                cursor.execute("""
                    SELECT user_message, assistant_response, timestamp
                    FROM conversation_turns
                    WHERE episode_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 5
                """, (episode_id,))
                
                turns = []
                for turn_row in cursor.fetchall():
                    turns.append({
                        "user": turn_row[0],
                        "assistant": turn_row[1],
                        "timestamp": turn_row[2]
                    })
                
                # Build a summary from recent turns if no LLM summary exists
                summary = row[3]
                if not summary and turns:
                    summary = f"Recent conversation: {turns[0]['user'][:50]}..." if turns else None
                
                episodes.append({
                    "id": episode_id,
                    "start_time": row[1],
                    "end_time": row[2],
                    "summary": summary,
                    "message_count": row[4],
                    "context_type": row[5],
                    "is_current": episode_id == current_episode,
                    "recent_turns": list(reversed(turns))  # Oldest first for context
                })
            conn.close()
            return {"current_episode": current_episode, "recent_episodes": episodes, "episode_count": len(episodes)}
        except Exception as e:
            logger.error(f"Error getting episode context: {e}")
            return {"error": str(e)}
    
    async def close_episode(self, user_id: str, context_type: str = "chat",
                            summary: Optional[str] = None) -> None:
        episode_id = await self._get_active_episode(user_id, context_type)
        if not episode_id:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE conversation_episodes 
                SET end_time = ?, summary = ?
                WHERE id = ?
            """, (datetime.now(), summary, episode_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error closing episode: {e}")
    
    async def _get_active_episode(self, user_id: str, context_type: str) -> Optional[int]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            timeout_minutes = 30 if context_type == "chat" else 120
            # Use SQLite datetime functions for proper comparison
            cursor.execute("""
                SELECT id FROM conversation_episodes 
                WHERE user_id = ? AND context_type = ? 
                AND end_time IS NULL 
                AND datetime(start_time) > datetime('now', '-{} minutes')
                ORDER BY start_time DESC 
                LIMIT 1
            """.format(timeout_minutes), (user_id, context_type))
            row = cursor.fetchone()
            conn.close()
            if row:
                logger.info(f"✅ Found active episode {row[0]} for user {user_id}")
            return row[0] if row else None
        except Exception as e:
            logger.error(f"Error getting active episode: {e}")
            return None
    
    async def _create_episode(self, user_id: str, context_type: str) -> Optional[str]:
        try:
            import uuid
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            timeout_minutes = 30 if context_type == "chat" else 120
            episode_id = str(uuid.uuid4())[:8]  # Short UUID for readability
            cursor.execute("""
                INSERT INTO conversation_episodes 
                (id, user_id, context_type, timeout_minutes, start_time)
                VALUES (?, ?, ?, ?, ?)
            """, (episode_id, user_id, context_type, timeout_minutes, datetime.now()))
            conn.commit()
            conn.close()
            logger.info(f"✅ Created episode {episode_id} for user {user_id}")
            return episode_id
        except Exception as e:
            logger.error(f"Error creating episode: {e}")
            return None
    
    async def _store_conversation_turn(self, episode_id: int, user_message: str, assistant_response: str, memory_fact_id: Optional[int] = None) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Store the actual conversation turn
            cursor.execute("""
                INSERT INTO conversation_turns (episode_id, user_message, assistant_response)
                VALUES (?, ?, ?)
            """, (episode_id, user_message, assistant_response))
            
            # Update episode message count
            cursor.execute("""
                UPDATE conversation_episodes 
                SET message_count = message_count + 1
                WHERE id = ?
            """, (episode_id,))
            
            if memory_fact_id:
                cursor.execute("""
                    UPDATE memory_facts 
                    SET episode_id = ?
                    WHERE id = ?
                """, (episode_id, memory_fact_id))
            conn.commit()
            conn.close()
            logger.info(f"✅ Stored conversation turn in episode {episode_id}")
        except Exception as e:
            logger.error(f"Error storing conversation turn: {e}")
    
    def _build_time_filter(self, time_range: str) -> str:
        if time_range == "today":
            return "AND DATE(mf.created_at) = DATE('now')"
        if time_range == "yesterday":
            return "AND DATE(mf.created_at) = DATE('now', '-1 day')"
        if time_range == "this_week":
            return "AND mf.created_at >= DATE('now', '-7 days')"
        if time_range == "last_week":
            return "AND mf.created_at >= DATE('now', '-14 days') AND mf.created_at < DATE('now', '-7 days')"
        if time_range == "this_month":
            return "AND mf.created_at >= DATE('now', '-30 days')"
        return ""

# Global instance
_temporal = TemporalMemoryIntegration()

# Facade API used by chat router
async def enhance_memory_search_with_temporal(query: str, user_id: str, time_range: str = "all") -> Dict:
    results = await _temporal.search_with_temporal_context(query, user_id, time_range, limit=10)
    context = await _temporal.get_episode_context(user_id, "chat")
    return {"temporal_results": results, "episode_context": context, "enhanced": True}

async def start_chat_episode(user_id: str, context_type: str = "chat") -> Optional[int]:
    return await _temporal.start_conversation_episode(user_id, context_type)

async def add_chat_turn(user_id: str, message: str, response: str, context_type: str = "chat",
                        memory_fact_id: Optional[int] = None) -> None:
    await _temporal.add_message_to_episode(user_id, message, response, context_type, memory_fact_id)

async def close_chat_episode(user_id: str, context_type: str = "chat", summary: Optional[str] = None) -> None:
    await _temporal.close_episode(user_id, context_type, summary)
