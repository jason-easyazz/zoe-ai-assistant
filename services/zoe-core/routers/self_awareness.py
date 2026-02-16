"""
Self-Awareness API Router
=========================

Provides endpoints for Zoe's self-awareness capabilities including
identity management, self-reflection, and consciousness monitoring.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import sys
sys.path.append('/app')
from self_awareness import self_awareness, SelfIdentity, SelfReflection, ConsciousnessState
from auth_integration import AuthenticatedSession, validate_session

router = APIRouter(prefix="/api/self-awareness", tags=["self-awareness"])

# Request/Response models
class IdentityUpdate(BaseModel):
    personality_traits: Optional[Dict[str, float]] = None
    core_values: Optional[List[str]] = None
    goals: Optional[List[str]] = None
    capabilities: Optional[List[str]] = None
    limitations: Optional[List[str]] = None

class InteractionData(BaseModel):
    user_message: str
    zoe_response: str
    response_time: float
    user_satisfaction: Optional[float] = None
    complexity: str = "medium"
    context: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None

class PerformanceMetrics(BaseModel):
    accuracy: float
    response_time: float
    user_satisfaction: float
    task_completion_rate: float
    summary: Optional[str] = None

@router.get("/identity")
async def get_identity(session: AuthenticatedSession = Depends(validate_session)):
    """Get Zoe's current identity and self-concept for the specified user"""
    try:
        # Set user context for privacy isolation
        self_awareness.set_user_context(user_id)
        
        identity_data = {
            "name": self_awareness.identity.name,
            "version": self_awareness.identity.version,
            "personality_traits": self_awareness.identity.personality_traits,
            "core_values": self_awareness.identity.core_values,
            "goals": self_awareness.identity.goals,
            "capabilities": self_awareness.identity.capabilities,
            "limitations": self_awareness.identity.limitations,
            "created_at": self_awareness.identity.created_at,
            "last_updated": self_awareness.identity.last_updated
        }
        return {"identity": identity_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/identity")
async def update_identity(identity_update: IdentityUpdate, session: AuthenticatedSession = Depends(validate_session)):
    """Update Zoe's identity for the specified user"""
    user_id = session.user_id
    try:
        # Set user context for privacy isolation
        self_awareness.set_user_context(user_id)
        
        if identity_update.personality_traits:
            self_awareness.identity.personality_traits.update(identity_update.personality_traits)
        
        if identity_update.core_values:
            self_awareness.identity.core_values = identity_update.core_values
        
        if identity_update.goals:
            self_awareness.identity.goals = identity_update.goals
        
        if identity_update.capabilities:
            self_awareness.identity.capabilities = identity_update.capabilities
        
        if identity_update.limitations:
            self_awareness.identity.limitations = identity_update.limitations
        
        self_awareness.save_identity()
        
        return {"message": "Identity updated successfully", "identity": {
            "name": self_awareness.identity.name,
            "version": self_awareness.identity.version,
            "last_updated": self_awareness.identity.last_updated
        }}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/self-description")
async def get_self_description(session: AuthenticatedSession = Depends(validate_session)):
    try:
        self_awareness.set_user_context(user_id)
        description = await self_awareness.get_self_description()
        return {"self_description": description}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reflect/interaction")
async def reflect_on_interaction(interaction_data: InteractionData, session: AuthenticatedSession = Depends(validate_session)):
    """Trigger self-reflection on a recent interaction"""
    user_id = session.user_id
    try:
        self_awareness.set_user_context(user_id)
        interaction_dict = interaction_data.dict()
        reflection = await self_awareness.reflect_on_interaction(interaction_dict)
        
        return {
            "reflection": {
                "id": reflection.id,
                "timestamp": reflection.timestamp,
                "type": reflection.reflection_type,
                "insights": reflection.insights,
                "action_items": reflection.action_items,
                "emotional_state": reflection.emotional_state,
                "confidence_level": reflection.confidence_level
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reflect/performance")
async def reflect_on_performance(performance_metrics: PerformanceMetrics, session: AuthenticatedSession = Depends(validate_session)):
    try:
        self_awareness.set_user_context(user_id)
        metrics_dict = performance_metrics.dict()
        reflection = await self_awareness.reflect_on_performance(metrics_dict)
        
        return {
            "reflection": {
                "id": reflection.id,
                "timestamp": reflection.timestamp,
                "type": reflection.reflection_type,
                "insights": reflection.insights,
                "action_items": reflection.action_items,
                "emotional_state": reflection.emotional_state,
                "confidence_level": reflection.confidence_level
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/reflections")
async def get_reflections(
    limit: int = Query(10, description="Number of reflections to return"),
    reflection_type: Optional[str] = Query(None, description="Filter by reflection type")
):
    """Get recent self-reflections"""
    user_id = session.user_id
    try:
        reflections = await self_awareness._get_recent_reflections(limit)
        
        if reflection_type:
            reflections = [r for r in reflections if r.reflection_type == reflection_type]
        
        return {
            "reflections": [{
                "id": r.id,
                "timestamp": r.timestamp,
                "type": r.reflection_type,
                "content": r.content,
                "insights": r.insights,
                "action_items": r.action_items,
                "emotional_state": r.emotional_state,
                "confidence_level": r.confidence_level
            } for r in reflections]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/consciousness/update")
async def update_consciousness(context: Dict[str, Any], session: AuthenticatedSession = Depends(validate_session)):
    try:
        consciousness = await self_awareness.update_consciousness(context)
        
        return {
            "consciousness": {
                "timestamp": consciousness.timestamp,
                "attention_focus": consciousness.attention_focus,
                "current_goals": consciousness.current_goals,
                "emotional_state": consciousness.emotional_state,
                "energy_level": consciousness.energy_level,
                "confidence": consciousness.confidence,
                "active_memories": consciousness.active_memories
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/consciousness/current")
async def get_current_consciousness(session: AuthenticatedSession = Depends(validate_session)):
    """Get Zoe's current consciousness state"""
    user_id = session.user_id
    try:
        if self_awareness.consciousness is None:
            # Initialize with default context
            context = {"current_task": "general_assistance"}
            consciousness = await self_awareness.update_consciousness(context)
        else:
            consciousness = self_awareness.consciousness
        
        return {
            "consciousness": {
                "timestamp": consciousness.timestamp,
                "attention_focus": consciousness.attention_focus,
                "current_goals": consciousness.current_goals,
                "emotional_state": consciousness.emotional_state,
                "energy_level": consciousness.energy_level,
                "confidence": consciousness.confidence,
                "active_memories": consciousness.active_memories
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/evaluation")
async def get_self_evaluation(session: AuthenticatedSession = Depends(validate_session)):
    try:
        evaluation = await self_awareness.self_evaluate()
        return {"evaluation": evaluation}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status")
async def get_self_awareness_status(session: AuthenticatedSession = Depends(validate_session)):
    """Get overall self-awareness system status"""
    user_id = session.user_id
    try:
        recent_reflections = await self_awareness._get_recent_reflections(limit=5)
        current_consciousness = self_awareness.consciousness
        
        status = {
            "system_active": True,
            "identity_loaded": self_awareness.identity is not None,
            "consciousness_active": current_consciousness is not None,
            "recent_reflections_count": len(recent_reflections),
            "last_reflection": recent_reflections[0].timestamp if recent_reflections else None,
            "current_emotional_state": current_consciousness.emotional_state if current_consciousness else "unknown",
            "current_confidence": current_consciousness.confidence if current_consciousness else 0.0
        }
        
        return {"status": status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/memories/self")
async def add_self_memory(
    memory_type: str = Query(..., description="Type of self-memory"),
    content: str = Query(..., description="Memory content"),
    importance: float = Query(5.0, description="Importance level 1-10"),
    tags: Optional[List[str]] = Query(None, description="Memory tags")
):
    """Add a memory about self"""
    try:
        import sqlite3
        conn = sqlite3.connect(self_awareness.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO self_memories (memory_type, content, importance, tags)
            VALUES (?, ?, ?, ?)
        """, (memory_type, content, importance, json.dumps(tags) if tags else None))
        
        memory_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {"memory_id": memory_id, "message": "Self-memory added successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/memories/self")
async def get_self_memories(
    memory_type: Optional[str] = Query(None, description="Filter by memory type"),
    limit: int = Query(20, description="Number of memories to return")
):
    """Get self-memories"""
    try:
        import sqlite3
        import json
        conn = sqlite3.connect(self_awareness.db_path)
        cursor = conn.cursor()
        
        query = "SELECT id, memory_type, content, importance, tags, created_at FROM self_memories"
        params = []
        
        if memory_type:
            query += " WHERE memory_type = ?"
            params.append(memory_type)
        
        query += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        memories = []
        
        for row in cursor.fetchall():
            memories.append({
                "id": row[0],
                "memory_type": row[1],
                "content": row[2],
                "importance": row[3],
                "tags": json.loads(row[4]) if row[4] else [],
                "created_at": row[5]
            })
        
        conn.close()
        return {"memories": memories}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/goals")
async def get_goals():
    """Get current goals and progress"""
    try:
        import sqlite3
        conn = sqlite3.connect(self_awareness.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT goal, status, progress, milestones, last_updated
            FROM goal_progress
            ORDER BY last_updated DESC
        """)
        
        goals = []
        for row in cursor.fetchall():
            goals.append({
                "goal": row[0],
                "status": row[1],
                "progress": row[2],
                "milestones": json.loads(row[3]) if row[3] else [],
                "last_updated": row[4]
            })
        
        conn.close()
        return {"goals": goals}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/goals")
async def add_goal(
    goal: str = Query(..., description="Goal description"),
    status: str = Query("active", description="Goal status"),
    progress: float = Query(0.0, description="Progress level 0-1"),
    milestones: Optional[List[str]] = Query(None, description="Goal milestones")
):
    """Add a new goal"""
    try:
        import sqlite3
        conn = sqlite3.connect(self_awareness.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO goal_progress (goal, status, progress, milestones)
            VALUES (?, ?, ?, ?)
        """, (goal, status, progress, json.dumps(milestones) if milestones else None))
        
        goal_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return {"goal_id": goal_id, "message": "Goal added successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/goals/{goal_id}")
async def update_goal(
    goal_id: int,
    status: Optional[str] = None,
    progress: Optional[float] = None,
    milestones: Optional[List[str]] = None
):
    """Update a goal"""
    try:
        import sqlite3
        conn = sqlite3.connect(self_awareness.db_path)
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        
        if progress is not None:
            updates.append("progress = ?")
            params.append(progress)
        
        if milestones is not None:
            updates.append("milestones = ?")
            params.append(json.dumps(milestones))
        
        if updates:
            updates.append("last_updated = CURRENT_TIMESTAMP")
            params.append(goal_id)
            
            query = f"UPDATE goal_progress SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(query, params)
            
            if cursor.rowcount == 0:
                conn.close()
                raise HTTPException(status_code=404, detail="Goal not found")
        
        conn.commit()
        conn.close()
        
        return {"message": "Goal updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
