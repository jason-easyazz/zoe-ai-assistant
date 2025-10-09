"""Samantha-Level Chat Router for Zoe v2.0 with RouteLLM + LiteLLM + mem-agent"""
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

# Import advanced components
from route_llm import router as route_llm_router
from mem_agent_client import MemAgentClient

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)

# Initialize mem-agent client for semantic search
try:
    mem_agent = MemAgentClient()
    logger.info("✅ mem-agent client initialized")
except Exception as e:
    logger.warning(f"❌ mem-agent initialization failed: {e}")
    mem_agent = None

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")  # Pydantic v2 syntax - allow extra fields
    
    message: str
    context: Optional[dict] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None  # Allow user_id in body for UI compatibility

# ============================================================================
# SAMANTHA-LEVEL MEMORY INTEGRATION
# ============================================================================

async def search_memories(query: str, user_id: str) -> Dict:
    """Search all memory sources using mem-agent (with SQLite fallback)"""
    memories = {"people": [], "projects": [], "notes": [], "conversations": [], "semantic_results": []}
    
    # Try mem-agent first for semantic search
    if mem_agent:
        try:
            result = await mem_agent.search(query, user_id=user_id, max_results=5)
            if not result.get("fallback"):
                memories["semantic_results"] = result.get("results", [])
                logger.info(f"✅ mem-agent returned {len(memories['semantic_results'])} semantic results")
            else:
                logger.info("mem-agent returned fallback signal, using SQLite")
        except Exception as e:
            logger.warning(f"mem-agent search failed, falling back to SQLite: {e}")
    
    # Vector search integration (FAISS/miniLM) as lightweight local semantic search
    try:
        from .vector_search import vector_engine
        vector_hits = vector_engine.search(query, search_type="all", limit=5, threshold=0.4)
        if vector_hits:
            # Map to simple dicts for prompt building
            memories["semantic_results"] += [
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

    # Also do traditional SQLite search for structured data
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
# LITELLM ROUTER INTEGRATION
# ============================================================================

async def intelligent_routing(message: str, context: Dict) -> Dict:
    """Use RouteLLM + LiteLLM Router for intelligent model selection"""
    try:
        # Use the actual RouteLLM router for classification
        routing_decision = await route_llm_router.route_query(message, context)
        
        # Map to our types
        model = routing_decision.get("model", "zoe-chat")
        requires_memory = routing_decision.get("requires_memory", False)
        
        # Determine type from model or keywords
        message_lower = message.lower()
        if requires_memory or any(word in message_lower for word in ['remember', 'who is', 'what did', 'recall']):
            routing_type = "memory-retrieval"
        elif any(word in message_lower for word in ['create', 'add', 'schedule', 'remind']):
            routing_type = "action"
        else:
            routing_type = "conversation"
        
        return {
            "model": model,
            "type": routing_type,
            "requires_memory": requires_memory or (routing_type == "memory-retrieval"),
            "confidence": routing_decision.get("confidence", 0.8),
            "reasoning": routing_decision.get("reasoning", "RouteLLM classification")
        }
    except Exception as e:
        logger.warning(f"RouteLLM routing failed, using fallback: {e}")
        # Fallback to simple heuristics
        message_lower = message.lower()
        if any(word in message_lower for word in ['remember', 'who is', 'what did', 'when did', 'recall']):
            return {"model": "zoe-memory", "type": "memory-retrieval", "requires_memory": True}
        elif any(word in message_lower for word in ['create', 'add', 'schedule', 'remind']):
            return {"model": "zoe-chat", "type": "action", "requires_memory": False}
        else:
            return {"model": "zoe-chat", "type": "conversation", "requires_memory": True}

def build_system_prompt(memories: Dict, user_context: Dict) -> str:
    """Build concise system prompt with context"""
    system_prompt = """You are Zoe, an AI assistant like Samantha from "Her" - warm, but direct and efficient.

RULES:
- When asked a question → Answer directly with facts from the context
- When chatting → Be friendly and conversational  
- Use bullet points for clarity
- No fluff or unnecessary questions - give them what they asked for
- Be concise but warm

"""
    
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

async def call_ollama_streaming(message: str, context: Dict, memories: Dict, user_context: Dict, routing: Dict):
    """Stream Ollama response token-by-token for instant feel"""
    system_prompt = build_system_prompt(memories, user_context)
    full_prompt = f"{system_prompt}\n\nUser's message: {message}\nZoe:"
    
    ollama_url = "http://zoe-ollama:11434/api/generate"
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            ollama_url,
            json={
                "model": "llama3.2:1b",  # Faster model for testing
                "prompt": full_prompt,
                "stream": False,  # Disable streaming for stability
                "options": {
                    "temperature": 0.8,
                    "top_p": 0.9,
                    "num_predict": 256,
                    "num_ctx": 2048
                }
            }
        ) as response:
            # Send initial metadata
            yield f"data: {json_module.dumps({'type': 'metadata', 'routing': routing.get('type'), 'memories': len(memories.get('people', []))})}\n\n"
            
            async for line in response.aiter_lines():
                if line:
                    try:
                        chunk = json_module.loads(line)
                        if chunk.get("response"):
                            # Send token
                            yield f"data: {json_module.dumps({'type': 'token', 'content': chunk['response']})}\n\n"
                        if chunk.get("done"):
                            # Send completion
                            yield f"data: {json_module.dumps({'type': 'done'})}\n\n"
                            break
                    except:
                        pass

async def call_ollama_with_context(message: str, context: Dict, memories: Dict, user_context: Dict) -> str:
    """Call Ollama with full Samantha-level context (OPTIMIZED, non-streaming)"""
    system_prompt = build_system_prompt(memories, user_context)
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
# MAIN CHAT ENDPOINT
# ============================================================================

from fastapi.responses import StreamingResponse
import json as json_module

@router.post("/api/chat/")
@router.post("/api/chat")
async def chat(
    msg: ChatMessage, 
    user_id: str = Query("default", description="User ID for privacy isolation"),
    stream: bool = Query(False, description="Enable streaming response")
):
    """Samantha-level chat with perfect memory, routing, and cross-system integration (with streaming!)"""
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
        
        # Step 1: Intelligent routing decision
        routing = await intelligent_routing(msg.message, context)
        
        # OPTIMIZATION: Run memory search and context gathering in parallel
        import asyncio
        if routing.get("requires_memory"):
            memories, user_context = await asyncio.gather(
                search_memories(msg.message, actual_user_id),
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
                "type": "memory_update",
                "data": {
                    "routing": routing.get("type"),
                    "query": msg.message,
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
        
        # Step 4: Generate response with full context
        if stream:
            # Return streaming response
            return StreamingResponse(
                call_ollama_streaming(msg.message, context, memories, user_context, routing),
                media_type="text/event-stream"
            )
        else:
            # Return regular response
            response = await call_ollama_with_context(msg.message, context, memories, user_context)
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
                "context_breakdown": {
                    "events": len(user_context.get("calendar_events", [])),
                    "journals": len(user_context.get("recent_journal", [])),
                    "people": len(user_context.get("people", [])),
                    "projects": len(user_context.get("projects", []))
                }
            }
        
    except Exception as e:
        logger.error(f"Chat error: {str(e)}", exc_info=True)
        return {"response": "I'm having a moment of confusion. Could you try asking that again?"}
