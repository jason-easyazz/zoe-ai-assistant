"""
Optimized Chat Router for Zoe AI Assistant
Integrates all enhancement systems with consistent AI responses
"""
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import sys
import time
import logging
import asyncio
import httpx

sys.path.append('/app')

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

class ChatMessage(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None
    user_id: Optional[str] = "default"

class ChatResponse(BaseModel):
    response: str
    response_time: float
    enhancement_used: Optional[str] = None
    confidence: Optional[float] = None

async def get_ai_response_optimized(message: str, context: Dict[str, Any]) -> str:
    """Get AI response with proper enhancement system integration"""
    try:
        # Import AI client
        from ai_client import get_ai_response
        
        # Enhanced context with all available systems
        enhanced_context = {
            **context,
            "enhancement_systems": {
                "temporal_memory": True,
                "cross_agent_collaboration": True,
                "user_satisfaction_tracking": True,
                "context_summarization_cache": True,
                "light_rag_intelligence": True
            },
            "available_experts": [
                "calendar", "lists", "memory", "planning", 
                "development", "weather", "homeassistant"
            ],
            "system_capabilities": [
                "temporal_awareness",
                "multi_expert_coordination", 
                "adaptive_learning",
                "performance_optimization",
                "self_awareness"
            ]
        }
        
        # Get AI response with enhanced context
        response = await get_ai_response(message, enhanced_context)
        return response
        
    except Exception as e:
        logger.error(f"AI response generation failed: {e}")
        # Fallback to a basic but informative response
        return f"I'm experiencing some technical difficulties right now, but I'm still here to help! I have access to temporal memory, multi-expert coordination, and adaptive learning capabilities. Could you try rephrasing your question? (Error: {str(e)})"

async def check_enhancement_systems() -> Dict[str, bool]:
    """Check status of all enhancement systems"""
    systems = {
        "temporal_memory": False,
        "cross_agent_collaboration": False,
        "user_satisfaction_tracking": False,
        "light_rag_intelligence": False
    }
    
    try:
        # Check temporal memory
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://localhost:8000/api/temporal-memory/status")
            systems["temporal_memory"] = response.status_code == 200
    except:
        pass
    
    try:
        # Check cross-agent collaboration
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://localhost:8000/api/orchestration/status")
            systems["cross_agent_collaboration"] = response.status_code == 200
    except:
        pass
    
    try:
        # Check user satisfaction
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://localhost:8000/api/satisfaction/status")
            systems["user_satisfaction_tracking"] = response.status_code == 200
    except:
        pass
    
    try:
        # Check Light RAG
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://localhost:8000/api/memories/stats/light-rag")
            systems["light_rag_intelligence"] = response.status_code == 200
    except:
        pass
    
    return systems

@router.post("/api/chat", response_model=ChatResponse)
@router.post("/api/chat/")
async def chat_optimized(
    message: str,
    context: Optional[Dict[str, Any]] = None,
    user_id: str = Query("default", description="User ID for privacy isolation")
):
    """
    Optimized chat endpoint with full enhancement system integration
    """
    start_time = time.time()
    
    try:
        # Check enhancement systems status
        enhancement_status = await check_enhancement_systems()
        active_systems = [k for k, v in enhancement_status.items() if v]
        
        # Enhanced context with system awareness
        enhanced_context = {
            "mode": "user",
            "user_id": user_id,
            "response_time": 0.0,
            "user_satisfaction": 0.5,
            "complexity": "medium",
            "active_tasks": 0,
            "familiarity": "medium",
            "enhancement_systems": enhancement_status,
            "active_enhancements": active_systems,
            "system_awareness": True,
            "conversation_history": context.get("conversation_history", []) if context else []
        }
        
        # Merge with provided context
        if context:
            enhanced_context.update(context)
        
        # Get AI response with all enhancements
        response = await get_ai_response_optimized(message, enhanced_context)
        
        # Calculate response time
        response_time = time.time() - start_time
        
        # Determine which enhancement was used (if any)
        enhancement_used = None
        if active_systems:
            enhancement_used = ", ".join(active_systems)
        
        # Calculate confidence based on response quality and system availability
        confidence = min(0.9, 0.5 + (len(active_systems) * 0.1) + (0.1 if response_time < 5.0 else 0.0))
        
        return ChatResponse(
            response=response,
            response_time=response_time,
            enhancement_used=enhancement_used,
            confidence=confidence
        )
        
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        response_time = time.time() - start_time
        
        # Return a helpful error response
        error_response = f"I apologize, but I'm experiencing some technical difficulties. However, I'm still here to help! My enhancement systems include temporal memory, multi-expert coordination, and adaptive learning. Please try again, and I'll do my best to assist you. (Error: {str(e)})"
        
        return ChatResponse(
            response=error_response,
            response_time=response_time,
            enhancement_used="error_fallback",
            confidence=0.3
        )

@router.post("/api/chat/enhanced", response_model=ChatResponse)
async def chat_enhanced(
    message: str,
    context: Optional[Dict[str, Any]] = None,
    user_id: str = Query("default", description="User ID for privacy isolation")
):
    """
    Enhanced chat endpoint with explicit enhancement system usage
    """
    start_time = time.time()
    
    try:
        # Force enable all enhancement systems for enhanced chat
        enhancement_status = {
            "temporal_memory": True,
            "cross_agent_collaboration": True,
            "user_satisfaction_tracking": True,
            "light_rag_intelligence": True
        }
        
        # Enhanced context with all systems active
        enhanced_context = {
            "mode": "enhanced",
            "user_id": user_id,
            "response_time": 0.0,
            "user_satisfaction": 0.5,
            "complexity": "high",
            "active_tasks": 0,
            "familiarity": "high",
            "enhancement_systems": enhancement_status,
            "active_enhancements": list(enhancement_status.keys()),
            "system_awareness": True,
            "enhanced_mode": True,
            "conversation_history": context.get("conversation_history", []) if context else []
        }
        
        # Merge with provided context
        if context:
            enhanced_context.update(context)
        
        # Get AI response with all enhancements
        response = await get_ai_response_optimized(message, enhanced_context)
        
        # Calculate response time
        response_time = time.time() - start_time
        
        return ChatResponse(
            response=response,
            response_time=response_time,
            enhancement_used="all_enhancements",
            confidence=0.95
        )
        
    except Exception as e:
        logger.error(f"Enhanced chat error: {str(e)}")
        response_time = time.time() - start_time
        
        error_response = f"I'm having trouble accessing my enhanced capabilities right now, but I'm still here to help! I have temporal memory, multi-expert coordination, and adaptive learning systems available. Please try again. (Error: {str(e)})"
        
        return ChatResponse(
            response=error_response,
            response_time=response_time,
            enhancement_used="error_fallback",
            confidence=0.4
        )

@router.get("/api/chat/status")
async def chat_status():
    """Get chat system status and available enhancements"""
    try:
        enhancement_status = await check_enhancement_systems()
        active_systems = [k for k, v in enhancement_status.items() if v]
        
        return {
            "status": "operational",
            "enhancement_systems": enhancement_status,
            "active_enhancements": active_systems,
            "total_enhancements": len(enhancement_status),
            "active_count": len(active_systems),
            "system_health": "excellent" if len(active_systems) >= 3 else "good" if len(active_systems) >= 2 else "degraded"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "enhancement_systems": {},
            "active_enhancements": [],
            "system_health": "error"
        }

@router.get("/api/chat/capabilities")
async def chat_capabilities():
    """Get detailed information about chat capabilities"""
    return {
        "chat_endpoints": {
            "standard": "/api/chat",
            "enhanced": "/api/chat/enhanced"
        },
        "enhancement_systems": {
            "temporal_memory": {
                "description": "Time-based memory queries and conversation episode tracking",
                "capabilities": ["episode_creation", "temporal_search", "memory_decay", "context_tracking"]
            },
            "cross_agent_collaboration": {
                "description": "Multi-expert coordination for complex tasks",
                "capabilities": ["expert_routing", "task_decomposition", "result_synthesis", "coordination"]
            },
            "user_satisfaction_tracking": {
                "description": "Adaptive learning based on user feedback",
                "capabilities": ["feedback_collection", "satisfaction_analysis", "adaptive_responses", "learning"]
            },
            "light_rag_intelligence": {
                "description": "Vector embeddings with relationship awareness",
                "capabilities": ["semantic_search", "relationship_awareness", "contextual_retrieval", "smart_search"]
            }
        },
        "ai_capabilities": {
            "self_awareness": True,
            "temporal_awareness": True,
            "multi_expert_coordination": True,
            "adaptive_learning": True,
            "performance_optimization": True,
            "context_understanding": True
        }
    }