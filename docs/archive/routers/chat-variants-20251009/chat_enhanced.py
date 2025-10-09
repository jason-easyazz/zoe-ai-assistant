"""
Enhanced Chat Router with Multi-Expert Model Integration
======================================================

Enhanced version of the chat router that uses the Enhanced MEM Agent
with Multi-Expert Model capabilities for both memory search AND action execution.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, List
import httpx
import logging
import sys
import sqlite3
import json
from datetime import datetime

sys.path.append('/app')

# Import enhanced components
from route_llm import router as route_llm_router
from enhanced_mem_agent_client import enhanced_mem_agent_client, enhanced_search_with_fallback

router = APIRouter(tags=["chat-enhanced"])
logger = logging.getLogger(__name__)

# Initialize enhanced mem-agent client
try:
    enhanced_mem_agent = enhanced_mem_agent_client
    logger.info("✅ Enhanced MEM Agent client initialized")
except Exception as e:
    logger.warning(f"❌ Enhanced MEM Agent initialization failed: {e}")
    enhanced_mem_agent = None

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")  # Pydantic v2 syntax - allow extra fields
    
    message: str
    context: Optional[dict] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None  # Allow user_id in body for UI compatibility

# ============================================================================
# ENHANCED MEMORY INTEGRATION WITH ACTION EXECUTION
# ============================================================================

async def enhanced_search_memories(query: str, user_id: str) -> Dict:
    """Enhanced search with Multi-Expert Model and action execution"""
    try:
        # Use Enhanced MEM Agent with action execution
        result = await enhanced_search_with_fallback(
            query=query,
            user_id=user_id,
            execute_actions=True
        )
        
        if result.get("enhanced"):
            # Enhanced MEM Agent succeeded
            experts = result.get("experts", [])
            actions_executed = result.get("actions_executed", 0)
            execution_summary = result.get("execution_summary", "")
            
            logger.info(f"Enhanced MEM Agent: {actions_executed} actions executed")
            
            # Extract memory results from experts
            memories = {
                "people": [],
                "projects": [], 
                "notes": [],
                "conversations": [],
                "semantic_results": [],
                "actions_executed": actions_executed,
                "execution_summary": execution_summary,
                "experts_used": [expert.get("expert") for expert in experts]
            }
            
            # Process expert results
            for expert_data in experts:
                expert_name = expert_data.get("expert", "")
                expert_result = expert_data.get("result", {})
                
                if expert_name == "memory" and expert_result.get("success"):
                    # Memory expert results
                    memories["semantic_results"] = expert_result.get("results", [])
                elif expert_result.get("success") and expert_result.get("action"):
                    # Action results - add to semantic results for context
                    memories["semantic_results"].append({
                        "entity": expert_name,
                        "content": expert_result.get("message", ""),
                        "action": expert_result.get("action", ""),
                        "success": True,
                        "score": expert_data.get("confidence", 0.8)
                    })
            
            return memories
        
        else:
            # Fallback to basic memory search
            logger.info("Using fallback memory search")
            return await basic_search_memories(query, user_id)
            
    except Exception as e:
        logger.error(f"Enhanced memory search failed: {e}")
        return await basic_search_memories(query, user_id)

async def basic_search_memories(query: str, user_id: str) -> Dict:
    """Fallback basic memory search"""
    memories = {
        "people": [],
        "projects": [],
        "notes": [],
        "conversations": [],
        "semantic_results": [],
        "actions_executed": 0,
        "execution_summary": "No actions executed (fallback mode)",
        "experts_used": []
    }
    
    # Vector search integration (FAISS/miniLM) as lightweight local semantic search
    try:
        from .vector_search import vector_engine
        vector_hits = vector_engine.search(query, search_type="all", limit=5, threshold=0.4)
        if vector_hits:
            memories["semantic_results"] = [
                {
                    "id": h.id,
                    "content": h.content,
                    "type": h.type,
                    "score": h.similarity_score,
                    "metadata": h.metadata,
                }
                for h in vector_hits
            ]
    except Exception as e:
        logger.warning(f"Vector search unavailable: {e}")

    # Traditional SQLite search for structured data
    db_path = "/app/data/zoe.db"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Search people
        cursor.execute("""
            SELECT name, relationship, notes, last_interaction 
            FROM people WHERE user_id = ? AND (name LIKE ? OR notes LIKE ?)
            ORDER BY last_interaction DESC LIMIT 5
        """, (user_id, f"%{query}%", f"%{query}%"))
        memories["people"] = [{"name": r[0], "relationship": r[1], "notes": r[2]} for r in cursor.fetchall()]
        
        # Search projects
        cursor.execute("""
            SELECT name, description, status FROM projects 
            WHERE user_id = ? AND (name LIKE ? OR description LIKE ?)
            LIMIT 5
        """, (user_id, f"%{query}%", f"%{query}%"))
        memories["projects"] = [{"name": r[0], "description": r[1], "status": r[2]} for r in cursor.fetchall()]
        
        # Search notes
        cursor.execute("""
            SELECT title, content, tags FROM notes 
            WHERE user_id = ? AND (title LIKE ? OR content LIKE ?)
            ORDER BY created_at DESC LIMIT 5
        """, (user_id, f"%{query}%", f"%{query}%"))
        memories["notes"] = [{"title": r[0], "content": r[1][:200]} for r in cursor.fetchall()]
        
        conn.close()
    except Exception as e:
        logger.error(f"SQLite search error: {e}")
    
    return memories

async def get_user_context(user_id: str) -> Dict:
    """Get comprehensive user context from all integrated systems"""
    db_path = "/app/data/zoe.db"
    context = {
        "calendar_events": [],
        "active_lists": [],
        "recent_journal": [],
        "people": [],
        "projects": []
    }
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get recent calendar events (this week)
        try:
            cursor.execute("""
                SELECT title, start_date, description FROM events 
                WHERE user_id = ? AND start_date >= date('now', '-7 days')
                ORDER BY start_date DESC LIMIT 5
            """, (user_id,))
            context["calendar_events"] = [{"title": r[0], "date": r[1], "desc": r[2]} for r in cursor.fetchall()]
            logger.info(f"Found {len(context['calendar_events'])} calendar events")
        except Exception as e:
            logger.error(f"Calendar events error: {e}")
        
        # Get active lists
        try:
            cursor.execute("""
                SELECT name, type FROM lists WHERE user_id = ? LIMIT 5
            """, (user_id,))
            context["active_lists"] = [{"name": r[0], "type": r[1]} for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Lists error: {e}")
        
        # Get recent journal entries
        try:
            cursor.execute("""
                SELECT title, mood FROM journal_entries 
                WHERE user_id = ? ORDER BY created_at DESC LIMIT 3
            """, (user_id,))
            context["recent_journal"] = [{"title": r[0], "mood": r[1]} for r in cursor.fetchall()]
            logger.info(f"Found {len(context['recent_journal'])} journal entries")
        except Exception as e:
            logger.error(f"Journal entries error: {e}")
        
        # Get key people
        try:
            cursor.execute("""
                SELECT name, relationship FROM people 
                WHERE user_id = ? ORDER BY last_interaction DESC LIMIT 5
            """, (user_id,))
            context["people"] = [{"name": r[0], "relationship": r[1]} for r in cursor.fetchall()]
            logger.info(f"Found {len(context['people'])} people")
        except Exception as e:
            logger.error(f"People error: {e}")
        
        # Get active projects
        try:
            cursor.execute("""
                SELECT name, status FROM projects 
                WHERE user_id = ? AND status != 'completed' LIMIT 5
            """, (user_id,))
            context["projects"] = [{"name": r[0], "status": r[1]} for r in cursor.fetchall()]
            logger.info(f"Found {len(context['projects'])} projects")
        except Exception as e:
            logger.error(f"Projects error: {e}")
        
        conn.close()
    except Exception as e:
        logger.error(f"Context fetch error: {e}")
    
    return context

# ============================================================================
# ENHANCED INTELLIGENT ROUTING
# ============================================================================

async def enhanced_intelligent_routing(message: str, context: Dict) -> Dict:
    """Enhanced routing with action detection"""
    try:
        # Use the actual RouteLLM router for classification
        routing_decision = await route_llm_router.route_query(message, context)
        
        # Map to our types
        model = routing_decision.get("model", "zoe-chat")
        requires_memory = routing_decision.get("requires_memory", False)
        
        # Enhanced action detection
        message_lower = message.lower()
        if any(word in message_lower for word in ["add", "create", "schedule", "plan", "organize"]):
            routing_type = "action"
        elif requires_memory or any(word in message_lower for word in ['remember', 'who is', 'what did', 'recall']):
            routing_type = "memory-retrieval"
        else:
            routing_type = "conversation"
        
        return {
            "model": model,
            "type": routing_type,
            "requires_memory": requires_memory or (routing_type == "memory-retrieval"),
            "confidence": routing_decision.get("confidence", 0.8),
            "reasoning": routing_decision.get("reasoning", "Enhanced RouteLLM classification"),
            "action_detected": routing_type == "action"
        }
    except Exception as e:
        logger.warning(f"Enhanced RouteLLM routing failed, using fallback: {e}")
        # Enhanced fallback to simple heuristics
        message_lower = message.lower()
        if any(word in message_lower for word in ['add', 'create', 'schedule', 'plan']):
            return {"model": "zoe-chat", "type": "action", "requires_memory": False, "action_detected": True}
        elif any(word in message_lower for word in ['remember', 'who is', 'what did', 'when did', 'recall']):
            return {"model": "zoe-memory", "type": "memory-retrieval", "requires_memory": True, "action_detected": False}
        else:
            return {"model": "zoe-chat", "type": "conversation", "requires_memory": True, "action_detected": False}

def build_enhanced_system_prompt(memories: Dict, user_context: Dict, routing: Dict) -> str:
    """Build enhanced system prompt with action awareness"""
    
    # Base prompt
    system_prompt = """You are Zoe, an AI assistant like Samantha from "Her" - warm, but direct and efficient.

RULES:
- When asked a question → Answer directly with facts from the context
- When chatting → Be friendly and conversational  
- When actions are requested → Acknowledge what was done
- Use bullet points for clarity
- No fluff or unnecessary questions - give them what they asked for
- Be concise but warm

"""
    
    # Add action awareness
    if memories.get("actions_executed", 0) > 0:
        system_prompt += f"ACTIONS COMPLETED:\n"
        system_prompt += f"• {memories.get('execution_summary', 'Actions were executed')}\n"
        system_prompt += f"• Used experts: {', '.join(memories.get('experts_used', []))}\n\n"
    
    # Add user's recent activities (condensed)
    if user_context.get("calendar_events"):
        system_prompt += "EVENTS THIS WEEK:\n"
        for event in user_context["calendar_events"][:3]:
            desc = event.get('desc', '')
            if desc:
                system_prompt += f"• {event['title']} ({event['date']}) - {desc[:50]}\n"
            else:
                system_prompt += f"• {event['title']} ({event['date']})\n"
    
    if user_context.get("recent_journal"):
        system_prompt += "\nRECENT JOURNAL:\n"
        for entry in user_context["recent_journal"][:2]:
            system_prompt += f"• {entry.get('title')} (Mood: {entry.get('mood')})\n"
    
    if user_context.get("projects"):
        system_prompt += "\nPROJECTS:\n"
        for proj in user_context["projects"][:2]:
            system_prompt += f"• {proj['name']} - {proj['status']}\n"
    
    if user_context.get("people"):
        system_prompt += "\nPEOPLE:\n"
        for p in user_context["people"][:2]:
            system_prompt += f"• {p['name']} ({p['relationship']})\n"
    
    return system_prompt

# ============================================================================
# ENHANCED OLLAMA INTEGRATION
# ============================================================================

async def call_ollama_with_enhanced_context(message: str, context: Dict, memories: Dict, user_context: Dict, routing: Dict) -> str:
    """Call Ollama with enhanced context including action results"""
    system_prompt = build_enhanced_system_prompt(memories, user_context, routing)
    full_prompt = f"{system_prompt}\n\nUser's message: {message}\nZoe:"
    
    try:
        # Use Docker network name for Ollama service
        ollama_url = "http://zoe-ollama:11434/api/generate"
        
        # PERFORMANCE OPTIMIZATION: Use faster 1b model with tuned settings
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                ollama_url,
                json={
                    "model": "llama3.2:1b",  # Faster model for testing: fast + high quality
                    "prompt": full_prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.8,
                        "top_p": 0.9,
                        "num_predict": 256,  # Limit response length for speed
                        "num_ctx": 2048      # Optimized context window
                    }
                }
            )
            data = response.json()
            return data.get("response", "I'm here to help!")
    except Exception as e:
        import traceback
        logger.error(f"Ollama error: {e}\n{traceback.format_exc()}")
        return "I'm having a moment of clarity brewing... Let me try that again!"

# ============================================================================
# ENHANCED CHAT ENDPOINT
# ============================================================================

@router.post("/api/chat/enhanced")
@router.post("/api/chat/enhanced/")
async def enhanced_chat(
    msg: ChatMessage, 
    user_id: str = Query("default", description="User ID for privacy isolation")
):
    """Enhanced chat with Multi-Expert Model and action execution"""
    try:
        import time
        start_time = time.time()
        
        # Use provided user_id from query param or body (for UI compatibility)
        actual_user_id = user_id if user_id != "default" else msg.user_id or "default"
        
        # Enhanced context
        context = {
            "mode": msg.context.get("mode", "user") if msg.context else "user",
            "user_id": actual_user_id,
            "session_id": msg.session_id or "default"
        }
        
        if msg.context:
            context.update(msg.context)
        
        # Step 1: Enhanced intelligent routing decision
        routing = await enhanced_intelligent_routing(msg.message, context)
        
        # Step 2: Enhanced memory search with action execution
        import asyncio
        if routing.get("requires_memory") or routing.get("action_detected"):
            memories, user_context = await asyncio.gather(
                enhanced_search_memories(msg.message, actual_user_id),
                get_user_context(actual_user_id)
            )
        else:
            memories = {}
            user_context = await get_user_context(actual_user_id)

        # Step 3: Emit intelligence stream context update
        try:
            # Lazy import to avoid circular
            from .notifications import stream_manager
            await stream_manager.broadcast({
                "type": "enhanced_memory_update",
                "data": {
                    "routing": routing.get("type"),
                    "query": msg.message,
                    "actions_executed": memories.get("actions_executed", 0),
                    "experts_used": memories.get("experts_used", []),
                    "context": {
                        "events": len(user_context.get("calendar_events", [])),
                        "journals": len(user_context.get("recent_journal", [])),
                        "people": len(user_context.get("people", [])),
                        "projects": len(user_context.get("projects", []))
                    }
                }
            })
        except Exception:
            pass
        
        # Step 4: Check if actions were executed and return expert message directly
        if memories.get("actions_executed", 0) > 0:
            # Actions were executed - return expert success message directly
            expert_messages = []
            for result in memories.get("semantic_results", []):
                if result.get("success") and result.get("content"):
                    expert_messages.append(result["content"])
            
            if expert_messages:
                # Use the expert's success message directly
                response = expert_messages[0]
            else:
                # Fallback to execution summary
                response = memories.get("execution_summary", "Action completed successfully.")
        else:
            # No actions executed - use LLM for conversation
            response = await call_ollama_with_enhanced_context(msg.message, context, memories, user_context, routing)
        
        response_time = time.time() - start_time
        
        # Count all memory sources used
        memory_count = (
            len(memories.get("people", [])) + 
            len(memories.get("projects", [])) +
            len(user_context.get("calendar_events", [])) +
            len(user_context.get("recent_journal", [])) +
            len(user_context.get("people", [])) +
            len(user_context.get("projects", []))
        )
        
        return {
            "response": response,
            "response_time": response_time,
            "routing": routing.get("type"),
            "memories_used": memory_count,
            "actions_executed": memories.get("actions_executed", 0),
            "execution_summary": memories.get("execution_summary", ""),
            "experts_used": memories.get("experts_used", []),
            "enhanced": True,
            "context_breakdown": {
                "events": len(user_context.get("calendar_events", [])),
                "journals": len(user_context.get("recent_journal", [])),
                "people": len(user_context.get("people", [])),
                "projects": len(user_context.get("projects", []))
            }
        }
        
    except Exception as e:
        logger.error(f"Enhanced chat error: {str(e)}", exc_info=True)
        return {"response": "I'm having a moment of confusion. Could you try asking that again?", "enhanced": False}
