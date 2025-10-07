"""
Temporal Memory API Router
==========================

Provides endpoints for temporal and episodic memory functionality.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
import sys
sys.path.append('/app')
from temporal_memory import temporal_memory, ConversationEpisode, TemporalMemory

router = APIRouter(prefix="/api/temporal-memory", tags=["temporal-memory"])

# Request/Response models
class EpisodeCreate(BaseModel):
    context_type: str = "general"
    participants: Optional[List[str]] = None

class EpisodeClose(BaseModel):
    generate_summary: bool = True

class TemporalSearch(BaseModel):
    query: str
    time_range: Optional[Tuple[str, str]] = None
    episode_id: Optional[str] = None

@router.post("/episodes")
async def create_episode(
    episode_data: EpisodeCreate,
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Create a new conversation episode"""
    try:
        episode = temporal_memory.create_episode(
            user_id=user_id,
            context_type=episode_data.context_type,
            participants=episode_data.participants
        )
        
        return {
            "episode": {
                "id": episode.id,
                "user_id": episode.user_id,
                "start_time": episode.start_time,
                "status": episode.status.value,
                "context_type": episode.context_type,
                "message_count": episode.message_count,
                "topics": episode.topics,
                "participants": episode.participants
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/episodes/active")
async def get_active_episode(
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Get the active episode for a user"""
    try:
        episode = temporal_memory.get_active_episode(user_id)
        
        if not episode:
            return {"episode": None}
        
        return {
            "episode": {
                "id": episode.id,
                "user_id": episode.user_id,
                "start_time": episode.start_time,
                "end_time": episode.end_time,
                "status": episode.status.value,
                "context_type": episode.context_type,
                "summary": episode.summary,
                "message_count": episode.message_count,
                "topics": episode.topics,
                "participants": episode.participants,
                "created_at": episode.created_at,
                "updated_at": episode.updated_at
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/episodes/{episode_id}/messages")
async def add_message_to_episode(
    episode_id: str,
    message: str = Query(..., description="Message content"),
    message_type: str = Query("user", description="Message type: user or assistant"),
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Add a message to an episode"""
    try:
        success = temporal_memory.add_message_to_episode(
            episode_id=episode_id,
            message=message,
            message_type=message_type
        )
        
        if success:
            return {"message": "Message added successfully"}
        else:
            raise HTTPException(status_code=400, detail="Failed to add message")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/episodes/{episode_id}/close")
async def close_episode(
    episode_id: str,
    close_data: EpisodeClose,
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Close an episode and optionally generate summary"""
    try:
        success = temporal_memory.close_episode(
            episode_id=episode_id,
            generate_summary=close_data.generate_summary
        )
        
        if success:
            return {"message": "Episode closed successfully"}
        else:
            raise HTTPException(status_code=400, detail="Failed to close episode")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/episodes/history")
async def get_episode_history(
    user_id: str = Query(..., description="User ID for privacy isolation"),
    limit: int = Query(10, description="Number of episodes to return")
):
    """Get episode history for a user"""
    try:
        episodes = temporal_memory.get_episode_history(user_id, limit)
        
        return {
            "episodes": [{
                "id": episode.id,
                "user_id": episode.user_id,
                "start_time": episode.start_time,
                "end_time": episode.end_time,
                "status": episode.status.value,
                "context_type": episode.context_type,
                "summary": episode.summary,
                "message_count": episode.message_count,
                "topics": episode.topics,
                "participants": episode.participants,
                "created_at": episode.created_at,
                "updated_at": episode.updated_at
            } for episode in episodes]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/search")
async def search_temporal_memories(
    search_data: TemporalSearch,
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Search memories with temporal context"""
    try:
        memories = temporal_memory.search_temporal_memories(
            query=search_data.query,
            user_id=user_id,
            time_range=search_data.time_range,
            episode_id=search_data.episode_id
        )
        
        return {
            "memories": [{
                "fact_id": memory.fact_id,
                "fact": memory.fact,
                "entity_type": memory.entity_type,
                "entity_id": memory.entity_id,
                "entity_name": memory.entity_name,
                "category": memory.category,
                "importance": memory.importance,
                "episode_id": memory.episode_id,
                "timestamp": memory.timestamp,
                "decay_factor": memory.decay_factor,
                "access_count": memory.access_count,
                "last_accessed": memory.last_accessed,
                "temporal_context": memory.temporal_context
            } for memory in memories]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/decay/apply")
async def apply_memory_decay(
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Apply memory decay algorithm to old memories"""
    try:
        result = temporal_memory.apply_memory_decay(user_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/timeouts/check")
async def check_episode_timeouts():
    """Check for episodes that should be closed due to timeout"""
    try:
        result = temporal_memory.check_episode_timeouts()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats")
async def get_temporal_memory_stats(
    user_id: str = Query(..., description="User ID for privacy isolation")
):
    """Get temporal memory statistics"""
    try:
        import sqlite3
        conn = sqlite3.connect(temporal_memory.db_path)
        cursor = conn.cursor()
        
        # Get episode counts
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM conversation_episodes
            WHERE user_id = ?
            GROUP BY status
        """, (user_id,))
        
        episode_counts = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Get memory counts with temporal metadata
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM memory_facts mf
            JOIN memory_temporal_metadata mtm ON mf.id = mtm.fact_id
            JOIN conversation_episodes ce ON mtm.episode_id = ce.id
            WHERE ce.user_id = ?
        """, (user_id,))
        
        temporal_memory_count = cursor.fetchone()[0]
        
        # Get average decay factor
        cursor.execute("""
            SELECT AVG(decay_factor) as avg_decay
            FROM memory_temporal_metadata mtm
            JOIN conversation_episodes ce ON mtm.episode_id = ce.id
            WHERE ce.user_id = ?
        """, (user_id,))
        
        avg_decay = cursor.fetchone()[0] or 1.0
        
        conn.close()
        
        return {
            "episode_counts": episode_counts,
            "temporal_memory_count": temporal_memory_count,
            "average_decay_factor": avg_decay,
            "decay_halflife_days": temporal_memory.decay_halflife_days,
            "episode_timeouts": temporal_memory.episode_timeouts
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



