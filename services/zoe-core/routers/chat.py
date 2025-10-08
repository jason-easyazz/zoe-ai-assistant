"""Samantha-Level Chat Router for Zoe v2.0 with RouteLLM + LiteLLM + Enhanced MEM Agent"""
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
from datetime import datetime

sys.path.append('/app')

# Import advanced components
from route_llm import router as route_llm_router
from mem_agent_client import MemAgentClient
from enhanced_mem_agent_client import EnhancedMemAgentClient
from model_config import model_selector, ModelConfig

# Import temporal memory integration
sys.path.append('/home/pi/zoe')
try:
    from temporal_memory_integration import (
        enhance_memory_search_with_temporal,
        start_chat_episode,
        add_chat_turn,
        close_chat_episode
    )
    TEMPORAL_MEMORY_AVAILABLE = True
    logger.info("✅ Temporal memory integration loaded successfully")
except ImportError as e:
    logger.warning(f"⚠️ Temporal memory integration not available: {e}")
    TEMPORAL_MEMORY_AVAILABLE = False
    
    # Fallback functions
    async def start_chat_episode(user_id: str, context_type: str = "chat"):
        return None
    async def add_chat_turn(user_id: str, message: str, response: str, context_type: str = "chat", memory_fact_id: Optional[int] = None):
        pass
    async def close_chat_episode(user_id: str, context_type: str = "chat", summary: Optional[str] = None):
        pass
    async def enhance_memory_search_with_temporal(query: str, user_id: str, time_range: str = "all"):
        return {"enhanced": False, "error": "Temporal memory not available"}

router = APIRouter(tags=["chat"])
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
    """Analyzes response quality in real-time for Samantha-level intelligence"""
    
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
# SAMANTHA-LEVEL MEMORY INTEGRATION
# ============================================================================

async def search_memories(query: str, user_id: str, time_range: str = "all") -> Dict:
    """Search all memory sources using mem-agent with temporal awareness"""
    memories = {"people": [], "projects": [], "notes": [], "conversations": [], "semantic_results": [], "temporal_results": []}
    
    # Enhanced temporal memory search
    if TEMPORAL_MEMORY_AVAILABLE:
        try:
            temporal_enhancement = await enhance_memory_search_with_temporal(query, user_id, time_range)
            if temporal_enhancement.get("enhanced"):
                memories["temporal_results"] = temporal_enhancement.get("temporal_results", {}).get("results", [])
                memories["episode_context"] = temporal_enhancement.get("episode_context", {})
                logger.info(f"✅ Temporal search found {len(memories['temporal_results'])} results")
        except Exception as e:
            logger.warning(f"Temporal search failed: {e}")
    else:
        logger.info("📝 Temporal memory not available, skipping temporal search")
    
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
    """Use RouteLLM + LiteLLM Router for intelligent model selection with MCP integration"""
    try:
        # Use the actual RouteLLM router for classification
        routing_decision = await route_llm_router.route_query(message, context)
        
        # Map to our types
        model = routing_decision.get("model", "zoe-chat")
        requires_memory = routing_decision.get("requires_memory", False)
        
        # Enhanced action detection with MCP integration
        message_lower = message.lower()
        
        # Direct action patterns that should trigger MCP tools
        action_patterns = [
            'add to', 'add ', 'create ', 'schedule ', 'remind ', 'set ', 'turn on', 'turn off',
            'list ', 'show ', 'get ', 'find ', 'search ', 'delete ', 'remove ', 'update ',
            'shopping list', 'todo list', 'calendar', 'event', 'task', 'note', 'remember',
            'who are', 'contacts', 'people', 'appointment', 'meeting', 'call', 'text'
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
        # Enhanced fallback heuristics
        message_lower = message.lower()
        action_patterns = ['add to', 'add ', 'create ', 'schedule ', 'remind ', 'shopping list', 'todo list', 'remember', 'who are', 'contacts', 'people', 'appointment', 'meeting']
        memory_patterns = ['remember', 'who is', 'what did', 'when did', 'recall']
        
        if any(pattern in message_lower for pattern in action_patterns):
            return {"model": "llama3.2:1b", "type": "action", "requires_memory": False, "mcp_tools_needed": True}
        elif any(pattern in message_lower for pattern in memory_patterns):
            return {"model": "zoe-memory", "type": "memory-retrieval", "requires_memory": True, "mcp_tools_needed": False}
        else:
            return {"model": "zoe-chat", "type": "conversation", "requires_memory": True, "mcp_tools_needed": False}

async def build_system_prompt(memories: Dict, user_context: Dict) -> str:
    """Build concise system prompt with context and MCP tools"""
    system_prompt = """You are Zoe, an AI assistant with Samantha's warmth from "Her". Be like your best friend who's also the best personal assistant ever.

You have access to powerful tools through MCP server. Use these exact formats:

SHOPPING LISTS:
- "add [item] to shopping list" → [TOOL_CALL:add_to_list:{"list_name":"shopping","task_text":"[item]","priority":"medium"}]
- "add [item] to [list name] list" → [TOOL_CALL:add_to_list:{"list_name":"[list name]","task_text":"[item]","priority":"medium"}]

CALENDAR EVENTS:
- "schedule [event] on [date]" → [TOOL_CALL:create_calendar_event:{"title":"[event]","date":"[date]","time":"10:00"}]
- "what's my schedule" → [TOOL_CALL:get_calendar_events:{"start_date":"today","end_date":"next_week"}]

PEOPLE:
- "add [name] as [relationship]" → [TOOL_CALL:create_person:{"name":"[name]","relationship":"[relationship]"}]
- "who are my contacts" → [TOOL_CALL:get_people:{}]

MEMORIES:
- "remember [something]" → [TOOL_CALL:search_memories:{"query":"[something]","max_results":5}]

GREETINGS:
- "hi" or "hello" → "Hi there, how are you?"
"""
    
    return system_prompt

# Streaming function removed - using main function instead

async def call_ollama_with_context(message: str, context: Dict, memories: Dict, user_context: Dict, routing: Dict = None) -> str:
    """Call Ollama with full Samantha-level context using flexible model selection"""
    system_prompt = await build_system_prompt(memories, user_context)
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
    user_id: str = Query("default", description="User ID for privacy isolation"),
    stream: bool = Query(False, description="Enable streaming response")
):
    """Samantha-level chat with perfect memory, routing, cross-system integration AND action execution!"""
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
        
        # Start temporal memory episode for this conversation
        episode_id = None
        if TEMPORAL_MEMORY_AVAILABLE:
            episode_id = await start_chat_episode(actual_user_id, "chat")
            logger.info(f"📝 Started temporal episode {episode_id} for user {actual_user_id}")
        else:
            logger.info("📝 Temporal memory not available, skipping episode creation")
        
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
                    
                    # Return expert success message directly
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
                    
                    # Record this conversation turn in temporal memory
                    if TEMPORAL_MEMORY_AVAILABLE:
                        try:
                            await add_chat_turn(actual_user_id, msg.message, response, "chat")
                            logger.info(f"📝 Recorded enhanced mem agent turn in temporal episode {episode_id}")
                        except Exception as e:
                            logger.warning(f"Failed to record temporal memory: {e}")
                    
                    return {
                        "response": response,
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
            
            # Record this conversation turn in temporal memory
            if TEMPORAL_MEMORY_AVAILABLE:
                try:
                    await add_chat_turn(actual_user_id, msg.message, response, "chat")
                    logger.info(f"📝 Recorded conversation turn in temporal episode {episode_id}")
                except Exception as e:
                    logger.warning(f"Failed to record temporal memory: {e}")
            
            return {
                "response": response,
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
        logger.error(f"Chat error: {str(e)}", exc_info=True)
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
