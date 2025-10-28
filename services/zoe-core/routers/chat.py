"""Intelligent Chat Router for Zoe v2.0 with RouteLLM + LiteLLM + Enhanced MEM Agent"""
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, List
import httpx
import logging
import sys
import sqlite3
import json
import re
import statistics
import asyncio
import time
from datetime import datetime
import os

# Setup logger IMMEDIATELY
logger = logging.getLogger(__name__)

# Add parent directory to path only if not already accessible
# This allows imports to work in both Docker (/app) and local dev environments
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Import advanced components
from route_llm import router as route_llm_router
from mem_agent_client import MemAgentClient
from enhanced_mem_agent_client import EnhancedMemAgentClient
from model_config import model_selector, ModelConfig

# Import training data collector
from training_engine.data_collector import training_collector

# Import enhanced prompts
from prompt_templates import build_enhanced_prompt

# Import RAG enhancements
from rag_enhancements import hybrid_search_engine, query_expander, reranker

# Import context optimization
from context_optimizer import context_selector, context_compressor, context_budgeter

# Import memory consolidation
from memory_consolidation import memory_consolidator

# Import graph engine
from graph_engine import graph_engine

# Import preference learning
from preference_learner import preference_learner

# Import temporal memory integration (REQUIRED - core feature)
from temporal_memory_integration import (
    TemporalMemoryIntegration,
)

# Initialize temporal memory system
temporal_memory = TemporalMemoryIntegration()

# Wrapper functions for backward compatibility
async def start_chat_episode(user_id: str, context_type: str = "chat") -> Optional[int]:
    """Start a new chat episode for conversation continuity"""
    try:
        return await temporal_memory.start_conversation_episode(user_id, context_type)
    except Exception as e:
        logger.error(f"Failed to start chat episode: {e}")
        return None

async def add_chat_turn(user_id: str, message: str, response: str, context_type: str = "chat", memory_fact_id: Optional[int] = None):
    """Add a conversation turn to the current episode"""
    try:
        await temporal_memory.add_message_to_episode(user_id, message, response, context_type, memory_fact_id)
    except Exception as e:
        logger.error(f"Failed to add chat turn: {e}")

async def close_chat_episode(user_id: str, context_type: str = "chat", summary: Optional[str] = None):
    """Close the current chat episode"""
    try:
        await temporal_memory.close_episode(user_id, context_type, summary)
    except Exception as e:
        logger.error(f"Failed to close chat episode: {e}")

async def enhance_memory_search_with_temporal(query: str, user_id: str, time_range: str = "all") -> Dict:
    """Enhance memory search with temporal context from recent episodes"""
    try:
        temporal_results = await temporal_memory.search_with_temporal_context(query, user_id, time_range)
        episode_context = await temporal_memory.get_episode_context(user_id, "chat")
        return {
            "enhanced": True,
            "temporal_results": temporal_results,
            "episode_context": episode_context
        }
    except Exception as e:
        logger.error(f"Temporal memory search failed: {e}")
        return {"enhanced": False, "error": str(e)}

logger.info("✅ Temporal memory integration initialized (REQUIRED)")

# Import user satisfaction tracking
try:
    from user_satisfaction import satisfaction_system
    SATISFACTION_TRACKING_AVAILABLE = True
    logger.info("✅ User satisfaction tracking loaded successfully")
except ImportError as e:
    logger.warning(f"⚠️ User satisfaction tracking not available: {e}")
    SATISFACTION_TRACKING_AVAILABLE = False
    satisfaction_system = None

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

# Initialize mem-agent client for semantic search
try:
    mem_agent = MemAgentClient()
    logger.info("✅ mem-agent client initialized")
except Exception as e:
    logger.warning(f"❌ mem-agent initialization failed: {e}")
    mem_agent = None

# Initialize enhanced mem-agent client for action execution
try:
    enhanced_mem_agent = EnhancedMemAgentClient()
    logger.info("✅ enhanced mem-agent client initialized")
except Exception as e:
    logger.warning(f"❌ enhanced mem-agent initialization failed: {e}")
    enhanced_mem_agent = None

class QualityAnalyzer:
    """Analyzes response quality in real-time for best-in-class intelligence"""
    
    @staticmethod
    def analyze_response(response_text: str, query_type: str) -> Dict[str, float]:
        """Analyze response quality and return scores"""
        
        # Basic quality metrics
        word_count = len(response_text.split())
        char_count = len(response_text)
        
        # Quality score (1-10)
        quality_score = 5.0  # Base score
        
        # Length appropriateness
        if 20 <= word_count <= 200:
            quality_score += 1.0
        elif word_count < 10:
            quality_score -= 2.0
        elif word_count > 500:
            quality_score -= 1.0
        
        # Coherence indicators
        coherence_words = ["because", "therefore", "however", "although", "since", "while"]
        if any(word in response_text.lower() for word in coherence_words):
            quality_score += 1.0
        
        # Completeness indicators
        if response_text.strip().endswith(('.', '!', '?')):
            quality_score += 0.5
        
        # Warmth score (1-10)
        warmth_score = 5.0  # Base score
        
        warm_words = ["wonderful", "amazing", "great", "happy", "excited", "love", "care", "support", "help", "glad", "pleasure"]
        cold_words = ["error", "cannot", "unable", "sorry", "unfortunately", "failed", "problem", "issue"]
        
        response_lower = response_text.lower()
        for word in warm_words:
            if word in response_lower:
                warmth_score += 0.5
        
        for word in cold_words:
            if word in response_lower:
                warmth_score -= 0.5
        
        # Samantha-like warmth
        if any(phrase in response_lower for phrase in ["i'm here", "happy to help", "glad to", "pleasure", "care about"]):
            warmth_score += 1.0
        
        # Intelligence score (1-10)
        intelligence_score = 5.0  # Base score
        
        # Problem-solving approach
        if any(word in response_lower for word in ["let's", "we can", "approach", "strategy", "method", "plan"]):
            intelligence_score += 1.0
        
        # Context awareness
        if any(word in response_lower for word in ["based on", "considering", "given that", "since", "because"]):
            intelligence_score += 1.0
        
        # Structured thinking
        if any(word in response_lower for word in ["first", "second", "then", "next", "finally", "step"]):
            intelligence_score += 1.0
        
        # Tool usage score (1-10)
        tool_usage_score = 5.0  # Base score
        
        # Check for tool calls
        tool_call_pattern = r'\[TOOL_CALL:([^:]+):(\{[^}]+\})\]'
        matches = re.findall(tool_call_pattern, response_text)
        
        if matches:
            tool_usage_score += 3.0  # Bonus for using tools
            
            # Check for proper JSON format
            for match in matches:
                try:
                    json.loads(match[1])
                    tool_usage_score += 1.0  # Bonus for proper JSON
                except:
                    tool_usage_score -= 1.0  # Penalty for malformed JSON
        
        # Tool-related language
        tool_words = ["schedule", "remind", "organize", "automate", "set up", "create", "manage"]
        for word in tool_words:
            if word in response_lower:
                tool_usage_score += 0.2
        
        # Normalize scores to 1-10 range
        quality_score = max(1.0, min(10.0, quality_score))
        warmth_score = max(1.0, min(10.0, warmth_score))
        intelligence_score = max(1.0, min(10.0, intelligence_score))
        tool_usage_score = max(1.0, min(10.0, tool_usage_score))
        
        return {
            "quality": quality_score,
            "warmth": warmth_score,
            "intelligence": intelligence_score,
            "tool_usage": tool_usage_score
        }

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")  # Pydantic v2 syntax - allow extra fields
    
    message: str
    context: Optional[dict] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None  # Allow user_id in body for UI compatibility

# ============================================================================
# ADVANCED MEMORY INTEGRATION
# ============================================================================

async def search_memories(query: str, user_id: str, time_range: str = "all") -> Dict:
    """Search all memory sources using hybrid search with reranking and graph expansion"""
    memories = {"people": [], "projects": [], "notes": [], "conversations": [], "semantic_results": [], "temporal_results": []}
    
    # ✅ NEW: Query expansion for better retrieval
    try:
        expanded_queries = await query_expander.expand_query(query)
        logger.info(f"🔍 Query expanded: {query} → {expanded_queries[:3]}")
    except Exception as e:
        logger.warning(f"Query expansion failed: {e}")
        expanded_queries = [query]
    
    # Enhanced temporal memory search (ALWAYS ACTIVE)
    try:
        temporal_enhancement = await enhance_memory_search_with_temporal(query, user_id, time_range)
        if temporal_enhancement.get("enhanced"):
            memories["temporal_results"] = temporal_enhancement.get("temporal_results", {}).get("results", [])
            memories["episode_context"] = temporal_enhancement.get("episode_context", {})
            logger.info(f"✅ Temporal search found {len(memories['temporal_results'])} results")
    except Exception as e:
        logger.warning(f"Temporal search failed: {e}")
    
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
        
        # Search people (use expanded queries)
        for exp_query in expanded_queries[:2]:  # Top 2 expansions
            cursor.execute("""
                SELECT name, profile, facts FROM people 
                WHERE user_id = ? AND name LIKE ?
                ORDER BY updated_at DESC LIMIT 5
            """, (user_id, f"%{exp_query}%"))
            for r in cursor.fetchall():
                profile = json.loads(r[1]) if r[1] else {}
                facts = json.loads(r[2]) if r[2] else {}
                if r[0] not in [p.get('name') for p in memories["people"]]:  # Deduplicate
                    notes = str(facts.get("notes", ""))
                    memories["people"].append({
                        "name": r[0], 
                        "relationship": profile.get("relationship", "contact"),
                        "notes": notes,
                        "content": notes
                    })
        
        # Search projects
        cursor.execute("""
            SELECT name, description, status FROM projects 
            WHERE user_id = ? AND (name LIKE ? OR description LIKE ?)
            LIMIT 5
        """, (user_id, f"%{query}%", f"%{query}%"))
        memories["projects"] = [{"name": r[0], "description": r[1], "status": r[2], "content": f"{r[0]} - {r[1]}"} for r in cursor.fetchall()]
        
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
    
    # ✅ NEW: Rerank all semantic results for better relevance
    try:
        all_semantic = (
            memories.get("semantic_results", []) + 
            memories.get("people", []) + 
            memories.get("projects", []) +
            memories.get("notes", [])
        )
        
        if all_semantic:
            reranked = reranker.rerank(query, all_semantic, top_k=10)
            memories["semantic_results"] = reranked
            logger.info(f"🎯 Reranked to {len(reranked)} best results")
    except Exception as e:
        logger.warning(f"Reranking failed: {e}")
    
    return memories

async def get_user_context(user_id: str, query: str = "") -> Dict:
    """Get comprehensive user context with smart selection"""
    db_path = "/app/data/zoe.db"
    context = {
        "calendar_events": [],
        "active_lists": [],
        "recent_journal": [],
        "people": [],
        "projects": [],
        "consolidated_summary": ""
    }
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # ✅ NEW: Get consolidated summary instead of all raw data (when available)
        try:
            consolidated = await memory_consolidator.get_consolidated_context(user_id, days_back=7)
            if consolidated:
                context["consolidated_summary"] = consolidated
                logger.info(f"✅ Using consolidated context ({len(consolidated)} chars)")
        except Exception as e:
            logger.warning(f"Consolidation not available: {e}")
        
        # Get recent calendar events (this week)
        try:
            cursor.execute("""
                SELECT title, start_date, start_time, description, created_at FROM events 
                WHERE user_id = ? AND start_date >= date('now', '-7 days')
                ORDER BY start_date DESC LIMIT 10
            """, (user_id,))
            context["calendar_events"] = [
                {"title": r[0], "date": r[1], "start_time": r[2], "desc": r[3], "created_at": r[4]} 
                for r in cursor.fetchall()
            ]
            logger.info(f"Found {len(context['calendar_events'])} calendar events")
        except Exception as e:
            logger.error(f"Calendar events error: {e}")
        
        # Get active lists
        try:
            cursor.execute("""
                SELECT name, list_type FROM lists WHERE user_id = ? LIMIT 5
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
                SELECT name, profile FROM people 
                WHERE user_id = ? ORDER BY updated_at DESC LIMIT 5
            """, (user_id,))
            context["people"] = []
            for r in cursor.fetchall():
                profile = json.loads(r[1]) if r[1] else {}
                context["people"].append({
                    "name": r[0], 
                    "relationship": profile.get("relationship", "contact")
                })
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
    
    # ✅ NEW: Apply smart context selection if query provided
    if query:
        try:
            selected_context = context_selector.select_best_context(
                query=query,
                calendar_events=context.get("calendar_events", []),
                journal_entries=context.get("recent_journal", []),
                people=context.get("people", []),
                projects=context.get("projects", []),
                memories=[],  # Handled separately in search_memories
                max_items_per_category=5
            )
            
            # Replace with smartly selected items
            context.update(selected_context)
            logger.info(f"🎯 Context optimized with smart selection")
        except Exception as e:
            logger.warning(f"Smart context selection failed: {e}")
    
    return context

# ============================================================================
# LITELLM ROUTER INTEGRATION
# ============================================================================

async def intelligent_routing(message: str, context: Dict) -> Dict:
    """Use RouteLLM + LiteLLM Router for intelligent model selection with MCP integration"""
    try:
        # Use the actual RouteLLM router for classification
        routing_decision = await route_llm_router.route_query(message, context)
        
        # Map to our types
        model = routing_decision.get("model", "zoe-chat")
        requires_memory = routing_decision.get("requires_memory", False)
        
        # Enhanced action detection with MCP integration
        message_lower = message.lower()
        
        # Comprehensive action patterns including natural language variants
        action_patterns = [
            # Direct commands
            'add to', 'add ', 'create ', 'schedule ', 'remind ', 'set ', 'turn on', 'turn off',
            'list ', 'show ', 'get ', 'find ', 'search ', 'delete ', 'remove ', 'update ',
            # Natural language variants
            "don't let me forget", "don't forget", "i need to buy", "i need to get",
            "put on my list", "put on the list", "add it to", "put it on",
            "i should buy", "i should get", "need to pick up", "pick up some",
            "grab some", "get some", "buy some", "purchase",
            # Shopping specific
            'shopping list', 'shopping', 'grocery list', 'groceries',
            # Calendar specific  
            'todo list', 'calendar', 'event', 'task', 'appointment', 'meeting',
            # People/contacts
            'who are', 'contacts', 'people', 'call', 'text', 'phone',
            # Memory operations
            'note', 'remember', 'save', 'store', 'keep track'
        ]
        
        memory_patterns = ['remember', 'who is', 'what did', 'recall', 'when did', 'how did']
        
        if any(pattern in message_lower for pattern in action_patterns):
            routing_type = "action"  # Use action routing for better tool calling
            requires_memory = False  # Actions don't need memory search
        elif any(pattern in message_lower for pattern in memory_patterns):
            routing_type = "memory-retrieval"
            requires_memory = True
        else:
            routing_type = "conversation"
            requires_memory = True
        
        # Use specific models for different tasks
        if routing_type == "action":
            model = "llama3.2:1b"  # Better for tool calling
        elif routing_type == "conversation":
            model = "gemma3:1b"    # Better for conversations
        
        return {
            "model": model,
            "type": routing_type,
            "requires_memory": requires_memory,
            "confidence": routing_decision.get("confidence", 0.8),
            "reasoning": routing_decision.get("reasoning", "Enhanced pattern matching"),
            "mcp_tools_needed": routing_type == "action"
        }
    except Exception as e:
        logger.warning(f"RouteLLM routing failed, using fallback: {e}")
        # Enhanced fallback heuristics with comprehensive patterns
        message_lower = message.lower()
        action_patterns = [
            'add to', 'add ', 'create ', 'schedule ', 'remind ', 'shopping list', 'shopping',
            'todo list', 'remember', 'who are', 'contacts', 'people', 'appointment', 'meeting',
            "don't let me forget", "i need to buy", "put on my list", "buy some", "get some"
        ]
        memory_patterns = ['remember', 'who is', 'what did', 'when did', 'recall', 'what is my', "what's my"]
        
        if any(pattern in message_lower for pattern in action_patterns):
            return {"model": "llama3.2:1b", "type": "action", "requires_memory": False, "mcp_tools_needed": True}
        elif any(pattern in message_lower for pattern in memory_patterns):
            return {"model": "zoe-memory", "type": "memory-retrieval", "requires_memory": True, "mcp_tools_needed": False}
        else:
            return {"model": "zoe-chat", "type": "conversation", "requires_memory": True, "mcp_tools_needed": False}

async def build_system_prompt(memories: Dict, user_context: Dict, routing_type: str = "conversation", user_id: str = "default") -> str:
    """Build enhanced system prompt with few-shot learning, context, learned preferences, and conversation history"""
    # ✅ NEW: Get user preferences
    try:
        user_preferences = await preference_learner.get_preferences(user_id)
    except:
        user_preferences = None
    
    # ✅ NEW: Extract episode context from memories for temporal continuity
    episode_context = memories.get("episode_context", {})
    recent_episodes = episode_context.get("recent_episodes", [])
    
    # ✅ NEW: Use enhanced prompts with examples, preferences, and conversation history
    return build_enhanced_prompt(memories, user_context, routing_type, user_preferences, recent_episodes)

async def call_ollama_streaming(message: str, context: Dict, memories: Dict, user_context: Dict, routing: Dict):
    """
    Stream response with AG-UI Protocol compliance
    AG-UI Event Types: https://github.com/ag-ui-protocol/ag-ui
    """
    import json
    
    async def generate():
        try:
            session_id = context.get('session_id', f"session_{datetime.now().timestamp()}")
            
            # AG-UI Event: session_start
            yield f"data: {json.dumps({'type': 'session_start', 'session_id': session_id, 'timestamp': datetime.now().isoformat()})}\n\n"
            
            # AG-UI Event: agent_state_delta (context enrichment)
            context_breakdown = {
                "events": len(user_context.get("calendar_events", [])),
                "journals": len(user_context.get("recent_journal", [])),
                "people": len(user_context.get("people", [])),
                "projects": len(user_context.get("projects", [])),
                "memories_found": len(memories.get('people', []))
            }
            yield f"data: {json.dumps({'type': 'agent_state_delta', 'state': {'context': context_breakdown, 'routing': routing.get('type', 'conversation'), 'model': 'selecting...'}, 'timestamp': datetime.now().isoformat()})}\n\n"
            
            # Build prompt with routing-specific template and user preferences
            user_id_for_prompt = context.get("user_id", "default")
            system_prompt = await build_system_prompt(memories, user_context, routing.get("type", "conversation"), user_id_for_prompt)
            full_prompt = f"{system_prompt}\n\nUser's message: {message}\nZoe:"
            
            # Select model
            query_type = routing.get("type", "conversation")
            selected_model = model_selector.select_model(query_type)
            model_config = model_selector.get_model_config(selected_model)
            
            logger.info(f"🤖 Streaming with model: {selected_model}")
            
            # AG-UI Event: agent_state_delta (model selected)
            yield f"data: {json.dumps({'type': 'agent_state_delta', 'state': {'model': selected_model, 'status': 'generating'}, 'timestamp': datetime.now().isoformat()})}\n\n"
            
            # Check if this requires tool calls via MCP
            tools_context = await get_mcp_tools_context()
            if tools_context and routing.get("requires_tools"):
                # AG-UI Event: action (tool call)
                yield f"data: {json.dumps({'type': 'action', 'name': 'mcp_tools', 'arguments': {{'query': message}}, 'status': 'running', 'timestamp': datetime.now().isoformat()})}\n\n"
            
            # Stream from Ollama
            ollama_url = "http://zoe-ollama:11434/api/generate"
            
            async with httpx.AsyncClient(timeout=model_config.timeout) as client:
                async with client.stream(
                    "POST",
                    ollama_url,
                    json={
                        "model": selected_model,
                        "prompt": full_prompt,
                        "stream": True,
                        "options": {
                            "temperature": model_config.temperature,
                            "top_p": model_config.top_p,
                            "num_predict": model_config.num_predict,
                            "num_ctx": model_config.num_ctx,
                            "repeat_penalty": model_config.repeat_penalty,
                            "stop": model_config.stop_tokens
                        }
                    }
                ) as response:
                    full_response = ""
                    async for line in response.aiter_lines():
                        if line.strip():
                            try:
                                chunk = json.loads(line)
                                if "response" in chunk:
                                    token = chunk["response"]
                                    full_response += token
                                    # AG-UI Event: message_delta (content streaming)
                                    yield f"data: {json.dumps({'type': 'message_delta', 'delta': token, 'timestamp': datetime.now().isoformat()})}\n\n"
                            except Exception as e:
                                logger.error(f"Error parsing chunk: {e}")
                    
                    # Parse for any tool calls in the response
                    if full_response:
                        tool_calls = await parse_and_execute_tool_calls(full_response, context.get("user_id", "default"))
                        if tool_calls != full_response:
                            # AG-UI Event: action_result (tool execution completed)
                            yield f"data: {json.dumps({'type': 'action_result', 'result': {{'executed': True, 'response': tool_calls}}, 'timestamp': datetime.now().isoformat()})}\n\n"
                    
                    # AG-UI Event: session_end
                    yield f"data: {json.dumps({'type': 'session_end', 'session_id': session_id, 'final_state': {'tokens': len(full_response), 'complete': True}, 'timestamp': datetime.now().isoformat()})}\n\n"
                    
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            # AG-UI Event: error
            yield f"data: {json.dumps({'type': 'error', 'error': {'message': str(e), 'code': 'STREAM_ERROR'}, 'timestamp': datetime.now().isoformat()})}\n\n"
    
    return generate()

async def call_ollama_with_context(message: str, context: Dict, memories: Dict, user_context: Dict, routing: Dict = None) -> str:
    """Call Ollama with full context using flexible model selection"""
    routing_type = routing.get("type", "conversation") if routing else "conversation"
    user_id_for_prompt = context.get("user_id", "default")
    system_prompt = await build_system_prompt(memories, user_context, routing_type, user_id_for_prompt)
    full_prompt = f"{system_prompt}\n\nUser's message: {message}\nZoe:"
    
    # Select the best model based on routing and performance
    query_type = routing.get("type", "conversation") if routing else "conversation"
    selected_model = model_selector.select_model(query_type)
    model_config = model_selector.get_model_config(selected_model)
    
    logger.info(f"🤖 Using model: {selected_model} ({model_config.category.value}) for {query_type}")
    logger.info(f"⏱️ Timeout set to: {model_config.timeout} seconds")
    
    try:
        # Use Docker network name for Ollama service
        ollama_url = "http://zoe-ollama:11434/api/generate"
        
        # Use flexible model configuration with proper timeout
        async with httpx.AsyncClient(timeout=model_config.timeout) as client:
            response = await client.post(
                ollama_url,
                json={
                    "model": selected_model,
                    "prompt": full_prompt,
                    "stream": False,
                    "options": {
                        "temperature": model_config.temperature,
                        "top_p": model_config.top_p,
                        "num_predict": model_config.num_predict,
                        "num_ctx": model_config.num_ctx,
                        "repeat_penalty": model_config.repeat_penalty,
                        "stop": model_config.stop_tokens
                    }
                }
            )
            data = response.json()
            response_text = data.get("response", "I'm here to help!")
            
            # Analyze response quality
            quality_scores = QualityAnalyzer.analyze_response(response_text, query_type)
            
            # Update performance metrics with quality data
            response_time = data.get("total_duration", 0) / 1e9  # Convert nanoseconds to seconds
            model_selector.update_performance(selected_model, response_time, True)
            
            # Record quality metrics
            await model_selector.record_quality_metrics(
                model_name=selected_model,
                response_time=response_time,
                success=True,
                quality_scores=quality_scores,
                query_type=query_type,
                user_id=user_context.get("user_id", "default")
            )
            
            return response_text
            
    except Exception as e:
        import traceback
        logger.error(f"Ollama error with {selected_model}: {e}\n{traceback.format_exc()}")
        
        # Update performance metrics for failure
        model_selector.update_performance(selected_model, model_config.timeout, False)
        
        # Try fallback model
        fallback_model = model_selector.get_fallback_model(selected_model)
        if fallback_model != selected_model:
            logger.info(f"🔄 Trying fallback model: {fallback_model}")
            try:
                fallback_config = model_selector.get_model_config(fallback_model)
                async with httpx.AsyncClient(timeout=fallback_config.timeout) as client:
                    response = await client.post(
                        ollama_url,
                        json={
                            "model": fallback_model,
                            "prompt": full_prompt,
                            "stream": False,
                            "options": {
                                "temperature": fallback_config.temperature,
                                "top_p": fallback_config.top_p,
                                "num_predict": fallback_config.num_predict,
                                "num_ctx": fallback_config.num_ctx,
                                "repeat_penalty": fallback_config.repeat_penalty,
                                "stop": fallback_config.stop_tokens
                            }
                        }
                    )
                    data = response.json()
                    response_text = data.get("response", "I'm here to help!")
                    
                    # Update performance metrics for fallback
                    response_time = data.get("total_duration", 0) / 1e9
                    model_selector.update_performance(fallback_model, response_time, True)
                    
                    return response_text
                    
            except Exception as fallback_error:
                logger.error(f"Fallback model {fallback_model} also failed: {fallback_error}")
                model_selector.update_performance(fallback_model, fallback_config.timeout, False)
        
        return "Hi there! 😊 I'm here and ready to help. How can I assist you today?"

# ============================================================================
# MCP SERVER INTEGRATION - PROPER TOOL CONTEXT
# ============================================================================

async def get_mcp_tools_context() -> str:
    """Get available MCP tools as context for the LLM"""
    try:
        import httpx
        
        async with httpx.AsyncClient() as client:
            # Get available tools from MCP server
            response = await client.post(
                "http://zoe-mcp-server:8003/tools/list",
                json={"_auth_token": "default", "_session_id": "default"},
                timeout=5.0
            )
            
            if response.status_code == 200:
                tools_data = response.json()
                tools = tools_data.get("tools", [])
                
                # Format tools as context for the LLM with usage instructions
                tools_context = "AVAILABLE TOOLS:\n"
                for tool in tools:
                    name = tool.get("name", "")
                    description = tool.get("description", "")
                    tools_context += f"• {name}: {description}\n"
                
                tools_context += """
TOOL USAGE INSTRUCTIONS:
When you need to use a tool, respond with: [TOOL_CALL:tool_name:{"param1":"value1","param2":"value2"}]
CRITICAL: The parameters MUST be valid JSON with double quotes around keys and values.
After tool execution, confirm the action to the user.

EXAMPLES:
- "Add bread to shopping list" → [TOOL_CALL:add_to_list:{"list_name":"shopping","task_text":"bread","priority":"medium"}] → "Added bread to your shopping list"
- "Turn on living room light" → [TOOL_CALL:control_home_assistant_device:{"entity_id":"light.living_room","action":"turn_on"}] → "Turned on the living room light"
- "Send message to Matrix" → [TOOL_CALL:send_matrix_message:{"room_id":"!room:matrix.org","message":"Hello!"}] → "Message sent to Matrix room"

IMPORTANT: Always use proper JSON format with double quotes. Never use single quotes or omit quotes around keys.
"""
                
                return tools_context
            else:
                return "AVAILABLE TOOLS: Basic memory and list management tools available."
                
    except Exception as e:
        logger.warning(f"Could not fetch MCP tools context: {e}")
        return "AVAILABLE TOOLS: Basic memory and list management tools available."

async def parse_and_execute_tool_calls(response_text: str, user_id: str) -> str:
    """Parse tool calls from LLM response and execute them"""
    import re
    import json
    
    # Pattern to match tool calls: [TOOL_CALL:tool_name:{"param":"value"}]
    tool_call_pattern = r'\[TOOL_CALL:([^:]+):(\{[^}]+\})\]'
    matches = re.findall(tool_call_pattern, response_text)
    
    if not matches:
        return response_text  # No tool calls found, return original response
    
    final_response = response_text
    
    for tool_name, params_json in matches:
        try:
            # Parse the JSON parameters
            params = json.loads(params_json)
            
            # Execute the tool
            result = await execute_mcp_tool(tool_name, params, user_id)
            
            if result.get("success"):
                # Replace the tool call with success message
                success_msg = result.get("message", f"Executed {tool_name} successfully")
                final_response = final_response.replace(
                    f"[TOOL_CALL:{tool_name}:{params_json}]",
                    success_msg
                )
            else:
                # Replace with error message
                error_msg = result.get("error", f"Failed to execute {tool_name}")
                final_response = final_response.replace(
                    f"[TOOL_CALL:{tool_name}:{params_json}]",
                    f"Error: {error_msg}"
                )
                
        except Exception as e:
            logger.error(f"Error executing tool call {tool_name}: {e}")
            final_response = final_response.replace(
                f"[TOOL_CALL:{tool_name}:{params_json}]",
                f"Error executing {tool_name}: {str(e)}"
            )
    
    return final_response

async def execute_mcp_tool(tool_name: str, arguments: dict, user_id: str) -> dict:
    """Execute a specific MCP tool"""
    try:
        import httpx
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://zoe-mcp-server:8003/tools/{tool_name}",
                json={
                    **arguments,
                    "user_id": user_id,
                    "_auth_token": "default",
                    "_session_id": "default"
                },
                timeout=10.0
            )
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "data": response.json(),
                    "tool_name": tool_name
                }
            else:
                return {
                    "success": False,
                    "error": f"Tool execution failed: {response.status_code}",
                    "tool_name": tool_name
                }
                
    except Exception as e:
        logger.error(f"MCP tool execution failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "tool_name": tool_name
        }

# ============================================================================
# MAIN CHAT ENDPOINT WITH ENHANCED MEM AGENT
# ============================================================================

from fastapi.responses import StreamingResponse
import json as json_module

@router.post("/api/chat/")
@router.post("/api/chat")
async def chat(
    msg: ChatMessage,
    stream: bool = Query(False, description="Enable streaming response"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """Intelligent chat with perfect memory, routing, cross-system integration AND action execution!"""
    user_id = session.user_id
    try:
        import time
        start_time = time.time()
        
        # ✅ NEW: Wrap entire request in timeout to prevent hangs
        try:
            async with asyncio.timeout(60):  # 60s max for entire request (increased for Pi 5 testing)
                return await _chat_handler(msg, user_id, stream, start_time)
        except asyncio.TimeoutError:
            logger.warning(f"Chat request timed out after 60s for user {user_id}")
            return {
                "response": "I'm processing a lot right now. Could you try that again? I want to make sure I give you a complete answer.",
                "response_time": 60.0,
                "routing": "timeout",
                "error": "request_timeout"
            }
    except Exception as e:
        logger.error(f"Chat error: {str(e)}", exc_info=True)
        return {"response": "I'm here to help! Could you rephrase that for me? I want to make sure I understand exactly what you need. 😊"}

async def _chat_handler(msg: ChatMessage, user_id: str, stream: bool, start_time: float):
    """Internal chat handler with timeout protection"""
    try:
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
        
        # Check for onboarding mode
        onboarding_mode = msg.context.get("onboarding_mode", False) if msg.context else False
        if onboarding_mode:
            context["onboarding_mode"] = True
            logger.info(f"🎓 Onboarding mode active for user {actual_user_id}")
        
        # Check for developer session queries (Phase 1: beads-inspired)
        session_query_patterns = [
            "what was i working on",
            "where was i",
            "what did i do",
            "resume work",
            "last session",
            "previous work",
            "what were you doing",
            "restore session"
        ]
        
        is_session_query = any(pattern in msg.message.lower() for pattern in session_query_patterns)
        if is_session_query:
            logger.info(f"🔍 Detected developer session query: {msg.message}")
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        "http://localhost:8000/api/developer/tasks/sessions/what-was-i-doing",
                        params={"user_id": actual_user_id},
                        timeout=5.0
                    )
                    if response.status_code == 200:
                        session_data = response.json()
                        if session_data.get("found"):
                            summary = session_data.get("summary", "")
                            if not stream:
                                return {"response": summary}
                            else:
                                async def session_stream():
                                    yield f"data: {json_module.dumps({'type': 'session_start', 'timestamp': datetime.now().isoformat()})}\n\n"
                                    yield f"data: {json_module.dumps({'type': 'message_delta', 'delta': summary})}\n\n"
                                    yield f"data: {json_module.dumps({'type': 'session_end', 'timestamp': datetime.now().isoformat()})}\n\n"
                                return StreamingResponse(session_stream(), media_type="text/event-stream")
            except Exception as e:
                logger.error(f"Error retrieving developer session: {e}")
                # Fall through to normal chat if session query fails
        
        # Start temporal memory episode for this conversation (ALWAYS ACTIVE)
        episode_id = await start_chat_episode(actual_user_id, "chat")
        if episode_id:
            logger.info(f"📝 Started temporal episode {episode_id} for user {actual_user_id}")
        
        # Step 0: Detect if this needs orchestration (planning requests)
        needs_orchestration = _is_planning_request(msg.message)
        
        if needs_orchestration and stream:
            logger.info(f"🎯 Detected planning request, using orchestrator: {msg.message}")
            from cross_agent_collaboration import orchestrator
            
            async def orchestration_stream():
                async for event in orchestrator.stream_orchestration(actual_user_id, msg.message, context):
                    yield f"data: {json_module.dumps(event, default=str)}\n\n"
            
            return StreamingResponse(orchestration_stream(), media_type="text/event-stream")
        
        # Step 1: Try Enhanced MEM Agent for action execution first
        if enhanced_mem_agent:
            try:
                logger.info(f"🤖 Trying Enhanced MEM Agent for: {msg.message}")
                memories = await enhanced_mem_agent.enhanced_search(
                    msg.message, 
                    user_id=actual_user_id,
                    execute_actions=True
                )
                
                # Check if actions were executed
                if memories.get("actions_executed", 0) > 0:
                    logger.info(f"✅ Enhanced MEM Agent executed {memories['actions_executed']} actions")
                    logger.info(f"📊 Enhanced MEM Agent response keys: {list(memories.keys())}")
                    logger.info(f"📊 Semantic results count: {len(memories.get('semantic_results', []))}")
                    
                    # Extract response AND data from expert results
                    response = None
                    expert_data = None
                    primary_expert = memories.get("primary_expert", "")
                    
                    # Get response and data from results
                    results_list = memories.get("results", [])
                    if results_list and len(results_list) > 0:
                        first_result = results_list[0]
                        logger.info(f"📊 First result keys: {first_result.keys() if isinstance(first_result, dict) else 'not dict'}")
                        
                        if isinstance(first_result, dict):
                            response = first_result.get("content", "")
                            expert_data = first_result.get("data", {})
                            logger.info(f"📊 Expert data found: {bool(expert_data)}, keys: {expert_data.keys() if expert_data else 'none'}")
                    
                    if not response:
                        response = memories.get("execution_summary", "Action completed.")
                    
                    # FORMAT actual data for user display
                    logger.info(f"📊 Primary expert: {primary_expert}, has data: {bool(expert_data)}")
                    
                    if expert_data and ("calendar" in primary_expert.lower() or "calendar" in msg.message.lower()):
                        # Use data from Enhanced MEM Agent (it already queried the calendar)
                        try:
                            logger.info(f"📊 Formatting calendar data from expert...")
                            today_events = expert_data.get("today_events", [])
                            upcoming_events = expert_data.get("upcoming_events", [])
                            total = expert_data.get("total_events", 0)
                            
                            logger.info(f"📊 Expert data: {len(today_events)} today, {len(upcoming_events)} upcoming")
                            
                            if today_events:
                                response += "\n\n📅 **Today's Events:**\n"
                                for event in today_events:
                                    # Get time from start_time field (format: "HH:MM")
                                    time_str = event.get("start_time", "All day")
                                    if time_str and time_str != "All day":
                                        # Format as 12-hour time
                                        try:
                                            hour = int(time_str.split(":")[0])
                                            minute = time_str.split(":")[1] if ":" in time_str else "00"
                                            ampm = "AM" if hour < 12 else "PM"
                                            hour_12 = hour % 12 or 12
                                            time_str = f"{hour_12}:{minute} {ampm}"
                                        except:
                                            pass
                                    title = event.get("title", "Untitled")
                                    response += f"• {time_str} - {title}\n"
                            else:
                                response += "\n\n📅 Your calendar is clear for today!"
                            
                            if upcoming_events:
                                response += "\n\n📆 **Upcoming Events:**\n"
                                for event in upcoming_events:
                                    date_str = event.get("start_date", "")
                                    title = event.get("title", "Untitled")
                                    response += f"• {date_str} - {title}\n"
                        except Exception as e:
                            logger.error(f"❌ Failed to format calendar data: {e}", exc_info=True)
                    
                    elif expert_data and ("planning" in primary_expert.lower() or "plan" in msg.message.lower()):
                        # Format planning data from expert
                        try:
                            logger.info(f"📊 Formatting planning data from expert...")
                            steps = expert_data.get("steps", [])
                            
                            if steps:
                                response += "\n\n📋 **Your Plan:**\n"
                                for i, step in enumerate(steps[:10], 1):
                                    # Handle different step formats
                                    if isinstance(step, dict):
                                        step_text = step.get("description", step.get("title", step.get("step", str(step))))
                                        priority = step.get("priority", "")
                                        if priority:
                                            response += f"{i}. {step_text} [{priority}]\n"
                                        else:
                                            response += f"{i}. {step_text}\n"
                                    else:
                                        response += f"{i}. {step}\n"
                                
                                response += "\n💡 **Tips:**\n"
                                response += "• Start with highest priority items\n"
                                response += "• Schedule breaks between tasks\n"
                                response += "• Review progress at end of day"
                        except Exception as e:
                            logger.error(f"❌ Failed to format planning data: {e}", exc_info=True)
                    
                    elif expert_data and ("list" in primary_expert.lower() or "shopping" in msg.message.lower() or "add" in msg.message.lower() or "remove" in msg.message.lower()):
                        # Format list data from expert (works for add, remove, and query)
                        try:
                            logger.info(f"📊 Formatting list data from expert...")
                            current_items = expert_data.get("current_items", [])
                            items_data = expert_data.get("items", [])
                            
                            # Use whichever data is available
                            items_to_show = current_items if current_items else items_data
                            
                            logger.info(f"📊 List data: {len(items_to_show)} items")
                            logger.info(f"📊 Items structure: {items_to_show[:3] if items_to_show else 'empty'}")
                            
                            # Only show list if there are items OR if it was a query/remove action
                            # (Don't show empty list after adding, only after removing or querying)
                            should_show_list = (
                                len(items_to_show) > 0 or 
                                "remove" in msg.message.lower() or 
                                "show" in msg.message.lower() or 
                                "what" in msg.message.lower()
                            )
                            
                            if should_show_list:
                                if items_to_show:
                                    response += f"\n\n🛒 **Shopping List** ({len(items_to_show)} items):\n"
                                    for item in items_to_show[:15]:
                                        if isinstance(item, dict):
                                            status = "✅" if item.get("completed") else "○"
                                            text = item.get("text", item.get("name", "Item"))
                                            response += f"{status} {text}\n"
                                        else:
                                            response += f"○ {item}\n"
                                else:
                                    response += "\n\n🛒 Your shopping list is currently empty."
                        except Exception as e:
                            logger.error(f"❌ Failed to format list data: {e}", exc_info=True)
                    
                    # Record in temporal memory (ALWAYS ACTIVE)
                    try:
                        await add_chat_turn(actual_user_id, msg.message, response, "chat")
                        logger.info(f"📝 Recorded enhanced mem agent turn in temporal episode {episode_id}")
                    except Exception as e:
                        logger.warning(f"Failed to record temporal memory: {e}")
                    
                    # ✅ NEW: Log action execution for training
                    interaction_id = None
                    try:
                        interaction_id = await training_collector.log_interaction({
                            "message": msg.message,
                            "response": response,
                            "context": context,
                            "routing_type": "action_executed",
                            "model_used": "enhanced_mem_agent",
                            "user_id": actual_user_id
                        })
                        logger.debug(f"📝 Logged action training interaction {interaction_id}")
                    except Exception as e:
                        logger.warning(f"Failed to log action training: {e}")
                    
                    # ✅ NEW: Record satisfaction for action-executed responses
                    if SATISFACTION_TRACKING_AVAILABLE and satisfaction_system:
                        try:
                            response_time_action = time.time() - start_time
                            final_interaction_id = interaction_id or f"interaction_{int(time.time() * 1000)}"
                            asyncio.create_task(
                                asyncio.to_thread(
                                    satisfaction_system.record_interaction,
                                    final_interaction_id,
                                    actual_user_id,
                                    msg.message,
                                    response,
                                    response_time_action,
                                    {"routing": "action_executed", "actions": memories.get("actions_executed", 0)}
                                )
                            )
                            logger.debug(f"📊 Queued satisfaction tracking for action interaction {final_interaction_id}")
                        except Exception as e:
                            logger.warning(f"Failed to queue satisfaction tracking for action: {e}")
                    
                    # CRITICAL FIX: Support streaming for action responses
                    if stream:
                        # Stream the action response using AG-UI protocol
                        async def stream_action_response():
                            import asyncio  # Import here for async streaming
                            try:
                                session_id = msg.session_id or f"session_{int(time.time() * 1000)}"
                                
                                # Event: session_start
                                yield f"data: {json.dumps({'type': 'session_start', 'session_id': session_id, 'timestamp': datetime.now().isoformat()})}\n\n"
                                
                                # Event: agent_state_delta (action executed)
                                yield f"data: {json.dumps({'type': 'agent_state_delta', 'state': {'routing': 'action_executed', 'actions': memories.get('actions_executed', 0)}, 'timestamp': datetime.now().isoformat()})}\n\n"
                                
                                # Event: action
                                yield f"data: {json.dumps({'type': 'action', 'name': 'enhanced_mem_agent', 'status': 'complete', 'timestamp': datetime.now().isoformat()})}\n\n"
                                
                                # Event: message_delta (stream response word by word)
                                words = response.split(' ')
                                for i, word in enumerate(words):
                                    yield f"data: {json.dumps({'type': 'message_delta', 'delta': word + (' ' if i < len(words) - 1 else ''), 'timestamp': datetime.now().isoformat()})}\n\n"
                                    await asyncio.sleep(0.05)  # Smooth streaming
                                
                                # Event: session_end
                                yield f"data: {json.dumps({'type': 'session_end', 'session_id': session_id, 'final_state': {'complete': True}, 'timestamp': datetime.now().isoformat()})}\n\n"
                            except Exception as e:
                                logger.error(f"Action streaming error: {e}")
                                yield f"data: {json.dumps({'type': 'error', 'error': {'message': str(e)}, 'timestamp': datetime.now().isoformat()})}\n\n"
                        
                        return StreamingResponse(
                            stream_action_response(),
                            media_type="text/event-stream",
                            headers={
                                "Cache-Control": "no-cache",
                                "X-Accel-Buffering": "no",
                                "Connection": "keep-alive"
                            }
                        )
                    else:
                        # Non-streaming response (JSON)
                        return {
                            "response": response,
                            "interaction_id": interaction_id,  # ✅ NEW: For feedback
                            "response_time": time.time() - start_time,
                            "routing": "action_executed",
                            "actions_executed": memories.get("actions_executed", 0),
                            "memories_used": len(memories.get("semantic_results", [])),
                            "episode_id": episode_id,
                            "context_breakdown": {
                                "events": 0,
                                "journals": 0,
                                "people": 0,
                                "projects": 0
                            }
                        }
                else:
                    logger.info("🔄 Enhanced MEM Agent found no actions to execute, falling back to conversation")
            except Exception as e:
                logger.warning(f"Enhanced MEM Agent failed, falling back to conversation: {e}")
        
        # Step 2: Normal conversation flow with MCP tools context
        # Intelligent routing decision
        routing = await intelligent_routing(msg.message, context)
        
        # OPTIMIZATION: Run memory search and context gathering in parallel with timeout
        import asyncio
        try:
            async with asyncio.timeout(15):  # 15s max for memory search (increased for Pi 5 testing)
                if routing.get("requires_memory"):
                    memories, user_context = await asyncio.gather(
                        search_memories(msg.message, actual_user_id),
                        get_user_context(actual_user_id, query=msg.message)  # ✅ Pass query for smart selection
                    )
                else:
                    # Get episode context even without full memory search (conversational continuity)
                    memories = {}
                    try:
                        temporal_enhancement = await enhance_memory_search_with_temporal("", actual_user_id, "all")
                        memories["episode_context"] = temporal_enhancement.get("episode_context", {})
                        logger.info(f"✅ Got episode context for conversation (no full memory search)")
                    except Exception as e:
                        logger.warning(f"Failed to get episode context: {e}")
                    user_context = await get_user_context(actual_user_id, query=msg.message)  # ✅ Pass query
        except asyncio.TimeoutError:
            logger.warning(f"Memory search timed out after 15s, using empty context")
            memories = {}
            # Still try to get episode context on timeout (lightweight operation)
            try:
                temporal_enhancement = await enhance_memory_search_with_temporal("", actual_user_id, "all")
                memories["episode_context"] = temporal_enhancement.get("episode_context", {})
            except:
                pass
            user_context = {"calendar_events": [], "active_lists": [], "recent_journal": [], "people": [], "projects": []}

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
            # Return streaming response with AG-UI protocol
            return StreamingResponse(
                await call_ollama_streaming(msg.message, context, memories, user_context, routing),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",  # Disable nginx buffering for real-time streaming
                    "Connection": "keep-alive"
                }
            )
        else:
            # Return regular response
            response = await call_ollama_with_context(msg.message, context, memories, user_context, routing)
            
            # Parse and execute any tool calls in the response
            response = await parse_and_execute_tool_calls(response, actual_user_id)
            
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
            
            # Record this conversation turn in temporal memory (ALWAYS ACTIVE)
            try:
                await add_chat_turn(actual_user_id, msg.message, response, "chat")
                logger.info(f"📝 Recorded conversation turn in temporal episode {episode_id}")
            except Exception as e:
                logger.warning(f"Failed to record temporal memory: {e}")
            
            # ✅ NEW: Log interaction for training
            interaction_id = None
            try:
                # Determine which model was used
                model_used = model_selector.select_model(routing.get("type", "conversation"))
                
                interaction_id = await training_collector.log_interaction({
                    "message": msg.message,
                    "response": response,
                    "context": context,
                    "routing_type": routing.get("type"),
                    "model_used": model_used,
                    "user_id": actual_user_id
                })
                
                # Update with quality scores
                quality_scores = QualityAnalyzer.analyze_response(response, routing.get("type", "conversation"))
                await training_collector.update_interaction_quality(interaction_id, quality_scores)
                
                logger.debug(f"📝 Logged training interaction {interaction_id}")
            except Exception as e:
                logger.warning(f"Failed to log training interaction: {e}")
            
            # ✅ NEW: Record user satisfaction (fire-and-forget, don't block response)
            if SATISFACTION_TRACKING_AVAILABLE and satisfaction_system:
                try:
                    final_interaction_id = interaction_id or f"interaction_{int(time.time() * 1000)}"
                    asyncio.create_task(
                        asyncio.to_thread(
                            satisfaction_system.record_interaction,
                            final_interaction_id,
                            actual_user_id,
                            msg.message,
                            response,
                            response_time,
                            {"routing": routing.get("type"), "memories_used": memory_count}
                        )
                    )
                    logger.debug(f"📊 Queued satisfaction tracking for interaction {final_interaction_id}")
                except Exception as e:
                    logger.warning(f"Failed to queue satisfaction tracking: {e}")
            
            return {
                "response": response,
                "interaction_id": interaction_id,  # ✅ NEW: For feedback tracking
                "response_time": response_time,
                "routing": routing.get("type"),
                "memories_used": memory_count,
                "episode_id": episode_id,
                "context_breakdown": {
                    "events": len(user_context.get("calendar_events", [])),
                    "journals": len(user_context.get("recent_journal", [])),
                    "people": len(user_context.get("people", [])),
                    "projects": len(user_context.get("projects", []))
                }
            }
    except Exception as e:
        logger.error(f"Chat handler error: {str(e)}", exc_info=True)
        return {"response": "I'm here to help! Could you rephrase that for me? I want to make sure I understand exactly what you need. 😊"}

@router.get("/api/models/performance")
async def get_model_performance():
    """Get current model performance metrics"""
    try:
        quality_analysis = model_selector.get_quality_analysis()
        model_list = model_selector.list_available_models()
        
        return {
            "quality_analysis": quality_analysis,
            "model_list": model_list,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Performance metrics error: {e}")
        return {"error": str(e)}

@router.get("/api/models/quality")
async def get_quality_metrics():
    """Get detailed quality metrics for all models"""
    try:
        analysis = model_selector.get_quality_analysis()
        
        # Calculate summary statistics
        total_calls = sum(model.get("total_calls", 0) for model in analysis.values())
        avg_quality = statistics.mean([model.get("quality_score", 0) for model in analysis.values() if model.get("quality_score", 0) > 0]) if analysis else 0
        avg_warmth = statistics.mean([model.get("warmth_score", 0) for model in analysis.values() if model.get("warmth_score", 0) > 0]) if analysis else 0
        avg_intelligence = statistics.mean([model.get("intelligence_score", 0) for model in analysis.values() if model.get("intelligence_score", 0) > 0]) if analysis else 0
        
        return {
            "summary": {
                "total_calls": total_calls,
                "avg_quality_score": avg_quality,
                "avg_warmth_score": avg_warmth,
                "avg_intelligence_score": avg_intelligence,
                "models_tracked": len(analysis)
            },
            "detailed_metrics": analysis,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Quality metrics error: {e}")
        return {"error": str(e)}

# Helper Functions
def _is_planning_request(message: str) -> bool:
    """Detect if message needs full orchestration (planning requests and complex multi-step tasks)"""
    orchestration_patterns = [
        # Planning requests
        "plan my day", "plan day", "help me plan", "organize my day",
        "organize my week", "plan my week", "what should i do",
        "how should i plan", "help plan", "organize day", "plan my",
        # Multi-step tasks
        "and then", "and also", "after that",
        # Multiple systems (calendar + lists, etc.)
        "calendar and", "list and", "remind and", "schedule and",
        "add to list and", "create event and",
        # Complex queries
        "all my", "show me everything", "what do i have",
        "plan my morning", "plan my afternoon", "plan my evening"
    ]
    message_lower = message.lower()
    return any(phrase in message_lower for phrase in orchestration_patterns)

# ============================================================================
# FEEDBACK & TRAINING ENDPOINTS
# ============================================================================

@router.post("/api/chat/feedback/{interaction_id}")
async def provide_feedback(
    interaction_id: str,
    feedback_type: str = Query(..., description="thumbs_up, thumbs_down, or correction"),
    corrected_response: Optional[str] = None,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Allow user to rate/correct Zoe's responses for training"""
    user_id = session.user_id
    try:
        if feedback_type == "correction":
            if not corrected_response:
                return {"error": "corrected_response required for correction feedback"}
            await training_collector.record_correction(interaction_id, corrected_response)
            return {
                "status": "correction_recorded",
                "message": "Thanks! Zoe will learn from this tonight."
            }
        elif feedback_type == "thumbs_up":
            await training_collector.record_positive_feedback(interaction_id)
            return {
                "status": "positive_feedback_recorded",
                "message": "Great! This will reinforce Zoe's learning."
            }
        elif feedback_type == "thumbs_down":
            await training_collector.record_negative_feedback(interaction_id)
            return {
                "status": "negative_feedback_recorded",
                "message": "Thanks for the feedback. Zoe will improve."
            }
        else:
            return {"error": "Invalid feedback_type"}
    except Exception as e:
        logger.error(f"Feedback recording error: {e}")
        return {"error": str(e)}

@router.get("/api/chat/training-stats")
async def get_training_stats(session: AuthenticatedSession = Depends(validate_session)):
    """Get training statistics for display in UI"""
    user_id = session.user_id
    try:
        stats = await training_collector.get_stats(user_id)
        return {
            "examples_collected_today": stats.get("today_count", 0),
            "corrections_this_week": stats.get("corrections", 0),
            "next_training_run": stats.get("next_training", "Not scheduled"),
            "current_adapter_score": stats.get("adapter_score", 0),
            "adapter_deployed": stats.get("adapter_deployed", False)
        }
    except Exception as e:
        logger.error(f"Training stats error: {e}")
        return {"error": str(e)}
