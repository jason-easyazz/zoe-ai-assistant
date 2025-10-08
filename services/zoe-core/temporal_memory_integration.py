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
        await self._store_conversation_turn(episode_id, memory_fact_id)
    
    async def search_with_temporal_context(self, query: str, user_id: str,
                                           time_range: str = "all", limit: int = 10) -> Dict:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            time_filter = self._build_time_filter(time_range)
            params = [user_id, f"%{query}%"]
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
            results: List[Dict] = []
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
            conn.close()
            logger.info(f"Temporal search found {len(results)} results for '{query}' ({time_range})")
            return {"query": query, "time_range": time_range, "results": results, "total_results": len(results)}
        except Exception as e:
            logger.error(f"Error in temporal search: {e}")
            return {"error": str(e)}
    
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
    
    async def _create_episode(self, user_id: str, context_type: str) -> Optional[int]:
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
    
    async def _store_conversation_turn(self, episode_id: int, memory_fact_id: Optional[int]) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
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
