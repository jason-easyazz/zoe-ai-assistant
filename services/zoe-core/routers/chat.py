"""Intelligent Chat Router for Zoe v2.0 with RouteLLM + LiteLLM + Enhanced MEM Agent"""
from fastapi import APIRouter, Query, Depends
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
import hashlib
from datetime import datetime
import os
from auth_integration import validate_session, AuthenticatedSession

# Setup logger IMMEDIATELY
logger = logging.getLogger(__name__)


def clean_llm_response(response: str) -> str:
    """Remove unwanted prefixes from LLM responses"""
    # Strip leading/trailing whitespace
    response = response.strip()
    
    # Remove common LLM prefixes
    prefixes_to_remove = ["Zoe:", "Response:", "Assistant:", "Thought:", "Action:"]
    for prefix in prefixes_to_remove:
        if response.startswith(prefix):
            response = response[len(prefix):].strip()
            break  # Only remove one prefix
    
    return response



# Add parent directory to path only if not already accessible
# This allows imports to work in both Docker (/app) and local dev environments
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Import advanced components
from route_llm import router as route_llm_router
from mem_agent_client import MemAgentClient
from enhanced_mem_agent_client import EnhancedMemAgentClient
from model_config import model_selector, ModelConfig, MODEL_CONFIGS
from llm_provider import get_llm_provider

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

# Import context caching
from context_cache import context_cache, CACHE_TTL

# Import preference learning
from preference_learner import preference_learner

# Import temporal memory integration (REQUIRED - core feature)
from temporal_memory_integration import (
    TemporalMemoryIntegration,
)

# Initialize temporal memory system
temporal_memory = TemporalMemoryIntegration()

# Import intent system (HassIL-based classification)
from intent_system.classifiers import UnifiedIntentClassifier, get_context_manager
from intent_system.executors import IntentExecutor

# Import P0 & P1 features
from config import FeatureFlags
from intent_system.validation import ContextValidator
from intent_system.formatters.response_formatter import ResponseFormatter
from intent_system.temperature_manager import TemperatureManager
from grounding_validator import GroundingValidator, FastGroundingValidator
from behavioral_memory import behavioral_memory

# Initialize intent system
USE_INTENT_SYSTEM = os.getenv("USE_INTENT_CHAT", "true").lower() == "true"
intent_classifier = UnifiedIntentClassifier(intents_dir="intent_system/intents/en") if USE_INTENT_SYSTEM else None
intent_executor = IntentExecutor() if USE_INTENT_SYSTEM else None
context_manager = get_context_manager() if USE_INTENT_SYSTEM else None

# Initialize P0/P1 validators
grounding_validator = GroundingValidator() if FeatureFlags.PLATFORM == "jetson" else FastGroundingValidator()

logger.info(f"Intent system enabled: {USE_INTENT_SYSTEM}")
FeatureFlags.log_feature_status()

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

router = APIRouter(prefix="", tags=["chat"])
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
            # ✅ FIX: Add timeout to prevent blocking on slow memory search
            try:
                result = await asyncio.wait_for(
                    mem_agent.search(query, user_id=user_id, max_results=5),
                    timeout=1.0  # ✅ PHASE 3.1: Reduced to 1s for faster fallback
                )
            except asyncio.TimeoutError:
                logger.warning("⚠️ Memory search timeout, using fallback")
                result = {"fallback": True, "people": [], "collections": []}
            except Exception as e:
                logger.warning(f"⚠️ Memory search error: {e}")
                result = {"fallback": True, "people": [], "collections": []}
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
        
        # ✅ CRITICAL FIX: Search self facts stored in people table (is_self=1)
        try:
            cursor.execute("""
                SELECT facts FROM people 
                WHERE user_id = ? AND is_self = 1
            """, (user_id,))
            result = cursor.fetchone()
            if result and result[0]:
                facts = json.loads(result[0])
                # Improved matching: extract keywords from query and match against fact keys
                query_lower = query.lower()
                query_words = set(query_lower.split())
                
                # For "What is my X?" queries, extract the X part
                import re
                what_is_my_match = re.search(r'what\s+(?:is|are)\s+my\s+([a-z_\s]+)', query_lower)
                if what_is_my_match:
                    target = what_is_my_match.group(1).strip().replace(' ', '_')
                    query_words.add(target)
                
                # Search through facts JSON for matches
                for fact_key, fact_value in facts.items():
                    fact_key_lower = fact_key.lower()
                    fact_value_lower = str(fact_value).lower()
                    
                    # Match if:
                    # 1. Query contains fact_key or fact_value
                    # 2. Any query word matches fact_key (e.g., "favorite" matches "favorite_color")
                    # 3. Query is asking about this fact (e.g., "what is my favorite color" matches "favorite_color")
                    matches = (
                        query_lower in fact_key_lower or 
                        query_lower in fact_value_lower or
                        any(word in fact_key_lower for word in query_words if len(word) > 2) or
                        fact_key_lower in query_lower
                    )
                    
                    if matches:
                        memories["semantic_results"].append({
                            "type": "self_fact",
                            "fact_key": fact_key,
                            "fact_value": fact_value,
                            "content": f"{fact_key}: {fact_value}",
                            "score": 0.95  # Very high score for user's own facts
                        })
                logger.info(f"✅ Found {len([m for m in memories['semantic_results'] if m.get('type') == 'self_fact'])} self facts")
        except Exception as e:
            logger.warning(f"self facts search failed: {e}")
        
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

async def get_user_context_with_validation(user_id: str, query: str = "", intent=None) -> Dict:
    """Get user context with P0-1 validation (skip if not needed)"""
    # P0-1: Context Validation
    if FeatureFlags.USE_CONTEXT_VALIDATION and intent:
        should_fetch = ContextValidator.should_retrieve_context(intent, query)
        if not should_fetch:
            logger.info(f"[P0-1] Context SKIPPED for {intent.name}")
            return {}
    
    # Fetch context normally
    return await get_user_context(user_id, query)

async def get_user_context(user_id: str, query: str = "") -> Dict:
    """Get comprehensive user context with smart selection and caching"""
    # ✅ PHASE 1.2: Check cache first
    cache_key = f"{user_id}_{hashlib.md5(query.encode()).hexdigest()[:8]}"
    cached = await context_cache.get("user_context", cache_key)
    if cached:
        logger.debug(f"✅ Using cached user context for {user_id}")
        return cached
    
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
    
    # ✅ CRITICAL FIX: Add Light RAG semantic search for memory retrieval
    if query:
        try:
            memories = await search_memories(query, user_id)
            context["semantic_results"] = memories.get("semantic_results", [])
            context["temporal_results"] = memories.get("temporal_results", [])
            context["conversations"] = memories.get("conversations", [])
            logger.info(f"✅ Light RAG: {len(context.get('semantic_results', []))} semantic results")
        except Exception as e:
            logger.warning(f"Light RAG search failed: {e}")
    
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
    
    # ✅ PHASE 1.2: Cache the result
    await context_cache.set("user_context", cache_key, context, CACHE_TTL["user_context"])
    
    return context

# ============================================================================
# LITELLM ROUTER INTEGRATION
# ============================================================================

async def intelligent_routing(message: str, context: Dict) -> Dict:
    """Use RouteLLM + LiteLLM Router for intelligent model selection with MCP integration and caching"""
    # ✅ PHASE 4: Cache routing decisions
    cache_key = hashlib.md5(f"{message}_{context.get('user_id', 'default')}".encode()).hexdigest()
    cached_routing = await context_cache.get("routing_decision", cache_key)
    if cached_routing:
        logger.debug(f"✅ Using cached routing decision")
        return cached_routing
    
    try:
        # ✅ OPTIMIZATION: Fast path for simple actions - skip RouteLLM overhead
        message_lower = message.lower()
        simple_action_keywords = ['add to', 'add ', 'create ', 'schedule ', 'show my', 'get my']
        if any(keyword in message_lower for keyword in simple_action_keywords):
            # Fast direct routing for simple actions
            routing_result = {
                "model": model_selector._get_best_action_model(),
                "type": "action",
                "confidence": 0.9,
                "reasoning": "Simple action detected (fast path)",
                "mcp_tools_needed": True,
                "route_llm_model": "zoe-action",
                "requires_memory": False
            }
            # Cache the result
            await context_cache.set("routing_decision", cache_key, routing_result, CACHE_TTL["routing_decision"])
            return routing_result
        
        # ✅ DUAL-MODEL ARCHITECTURE: Use phi3:mini for routing decisions (fast CPU model)
        routing_model = model_selector.get_routing_model()  # Always phi3:mini
        routing_decision = await route_llm_router.route_query(message, context, routing_model=routing_model)
        
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
            # Conversational shopping (NEW - for 100% success!)
            "we're out of", "we are out of", "running low on", "running low",
            "i noticed we need", "noticed we need", "could use some", "could use",
            "i'm going to need", "going to need", "better get",
            # Shopping specific
            'shopping list', 'shopping', 'grocery list', 'groceries',
            # Calendar specific  
            'todo list', 'calendar', 'event', 'task', 'appointment', 'meeting',
            # People/contacts
            'who are', 'contacts', 'people', 'call', 'text', 'phone',
            # Memory storage (personal facts)
            'my favorite', 'i work as', 'i am a', 'my job is', 'my car is',
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
        
        # Use RouteLLM's model recommendation, but map to our model selector
        # RouteLLM returns model names like "zoe-chat", "zoe-memory", "zoe-action"
        # We'll use model_selector to get the actual model name
        route_model = routing_decision.get("model", "zoe-chat")
        
        # Map RouteLLM model names to query types for model_selector
        if route_model == "zoe-action" or routing_type == "action":
            # Use model selector for actions (will return qwen for tool calling)
            model = model_selector._get_best_action_model()
        elif route_model == "zoe-memory" or routing_type == "memory-retrieval":
            # Use model selector for memory (will return balanced model)
            model = model_selector._get_best_memory_model()
        else:
            # Use model selector for conversations (will return qwen or gemma)
            model = model_selector._get_best_conversation_model()
        
        # Cache the result
        routing_result = {
            "model": model,  # Now uses actual model name from model_selector
            "type": routing_type,
            "requires_memory": requires_memory,
            "confidence": routing_decision.get("confidence", 0.8),
            "reasoning": f"RouteLLM: {routing_decision.get('reasoning', 'Classification')} → ModelSelector: {model}",
            "mcp_tools_needed": routing_type == "action",
            "route_llm_model": route_model  # Keep original RouteLLM model name for reference
        }
        await context_cache.set("routing_decision", cache_key, routing_result, CACHE_TTL["routing_decision"])
        return routing_result
    except Exception as e:
        logger.warning(f"RouteLLM routing failed, using fallback: {e}")
        # Enhanced fallback heuristics with comprehensive patterns
        message_lower = message.lower()
        action_patterns = [
            'add to', 'add ', 'create ', 'schedule ', 'remind ', 'shopping list', 'shopping',
            'todo list', 'remember', 'who are', 'contacts', 'people', 'appointment', 'meeting',
            "don't let me forget", "i need to buy", "put on my list", "buy some", "get some",
            "we're out of", "running low", "noticed we need", "could use", "going to need",
            "my favorite", "i work as", "i am a", "my job is", "my car is",
            "my name is", "i'm called", "i'm named", "call me", "i live in", "i'm from", "i am from"
        ]
        memory_patterns = ['remember', 'who is', 'what did', 'when did', 'recall', 'what is my', "what's my", "what do i", "where do i", "where am i"]
        
        # Fallback: Use model_selector for actual model names (already imported at top)
        if any(pattern in message_lower for pattern in action_patterns):
            return {
                "model": model_selector._get_best_action_model(), 
                "type": "action", 
                "requires_memory": False, 
                "mcp_tools_needed": True
            }
        elif any(pattern in message_lower for pattern in memory_patterns):
            return {
                "model": model_selector._get_best_memory_model(), 
                "type": "memory-retrieval", 
                "requires_memory": True, 
                "mcp_tools_needed": False
            }
        else:
            return {
                "model": model_selector._get_best_conversation_model(), 
                "type": "conversation", 
                "requires_memory": True, 
                "mcp_tools_needed": False
            }

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

async def generate_streaming_response(message: str, context: Dict, memories: Dict, user_context: Dict, routing: Dict, tools_context: str = ""):
    """
    Stream response from LLM inference server with AG-UI Protocol compliance
    AG-UI Event Types: https://github.com/ag-ui-protocol/ag-ui
    """
    import json
    
    async def generate():
        session_id = context.get('session_id', f"session_{datetime.now().timestamp()}")
        user_id_for_prompt = context.get("user_id", "default")
        
        try:
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
            
            # 🎯 NEW: INTENT-FIRST CLASSIFICATION (Tier 0/1 - HassIL/Keywords)
            # Try intent classification before LLM routing
            if USE_INTENT_SYSTEM and intent_classifier and intent_executor:
                try:
                    start_intent_time = time.time()
                    
                    # Classify intent using HassIL (Tier 0) or Keywords (Tier 1)
                    intent = intent_classifier.classify(message)
                    
                    if intent and intent.confidence >= 0.7:
                        # Intent matched! Execute directly (10ms target)
                        intent_latency_ms = (time.time() - start_intent_time) * 1000
                        logger.info(
                            f"🎯 INTENT MATCH: {intent.name}, "
                            f"tier: {intent.tier}, "
                            f"confidence: {intent.confidence:.2f}, "
                            f"latency: {intent_latency_ms:.2f}ms"
                        )
                        
                        # Execute intent
                        result = await intent_executor.execute(intent, user_id_for_prompt, session_id)
                        
                        # Stream intent result
                        total_latency_ms = (time.time() - start_intent_time) * 1000
                        yield f"data: {json.dumps({'type': 'agent_state_delta', 'state': {'model': f'intent-tier-{intent.tier}', 'engine': 'hassil' if intent.tier == 0 else 'keywords', 'status': 'complete', 'latency_ms': total_latency_ms}, 'timestamp': datetime.now().isoformat()})}\n\n"
                        
                        # Stream response word by word for UX consistency
                        for word in result.message.split():
                            yield f"data: {json.dumps({'type': 'message_delta', 'delta': word + ' ', 'timestamp': datetime.now().isoformat()})}\n\n"
                        
                        yield f"data: {json.dumps({'type': 'session_end', 'session_id': session_id, 'final_state': {'intent': intent.name, 'tier': intent.tier, 'latency_ms': total_latency_ms, 'complete': True}, 'timestamp': datetime.now().isoformat()})}\n\n"
                        
                        logger.info(f"⚡ INTENT EXECUTION COMPLETE: {total_latency_ms:.2f}ms (target: <10ms for Tier 0)")
                        return  # Skip LLM entirely!
                    else:
                        intent_latency_ms = (time.time() - start_intent_time) * 1000
                        logger.debug(f"No intent match, falling back to LLM (classification took {intent_latency_ms:.2f}ms)")
                        
                except Exception as e:
                    logger.warning(f"Intent classification failed, falling back to LLM: {e}")
            
            # FALLBACK: Original routing logic
            # 🚀 GENIUS: STREAMING - Apply same minimal prompts (like non-streaming)
            routing_type = routing.get("type", "conversation")
            is_greeting = len(message) < 20 and any(g in message.lower() for g in ["hi", "hello", "hey", "morning", "afternoon", "evening", "thanks", "thank you", "bye", "goodnight"])
            is_action = routing_type == "action" or routing.get("mcp_tools_needed", False)
            
            # 🎯 THREE-TIER ROUTING: Smart action detection
            # Tier 1: Simple greeting → Fast LLM (no tools)
            # Tier 2: Single clear action → Direct MemAgent (NO LLM!)  ⚡
            # Tier 3: Complex multi-step → Advanced LLM + tools
            
            # Check if this is a simple, deterministic action
            simple_action_result = await _try_direct_memagent_action(message, user_id_for_prompt, is_action)
            
            if simple_action_result:
                # ✅ Action executed directly by MemAgent - NO LLM needed!
                logger.info(f"⚡ DIRECT EXECUTION: MemAgent handled action without LLM")
                yield f"data: {json.dumps({'type': 'agent_state_delta', 'state': {'model': 'memagent-direct', 'engine': 'pattern-match', 'status': 'complete'}, 'timestamp': datetime.now().isoformat()})}\n\n"
                yield f"data: {json.dumps({'type': 'message_delta', 'delta': simple_action_result, 'timestamp': datetime.now().isoformat()})}\n\n"
                yield f"data: {json.dumps({'type': 'session_end', 'session_id': session_id, 'final_state': {'tokens': len(simple_action_result.split()), 'complete': True}, 'timestamp': datetime.now().isoformat()})}\n\n"
                return  # Skip LLM entirely!
            
            # Fallback: Use LLM (existing flow)
            injected_tool_call = _auto_inject_tool_call(message)
            injected_execution_result = None
            logger.info(f"🔍 AUTO-INJECT DEBUG: message='{message}', is_action={is_action}, injected={injected_tool_call is not None}")
            if injected_tool_call and is_action:
                logger.info(f"🎯 STREAMING: AUTO-INJECTED tool call: {injected_tool_call[:80]}...")
                try:
                    injected_response = await parse_and_execute_code_or_tools(injected_tool_call, user_id_for_prompt)
                    logger.info(f"✅ STREAMING: Executed auto-injected tool call: {injected_response[:100]}")
                    injected_execution_result = injected_response
                except Exception as e:
                    logger.warning(f"❌ STREAMING: Failed to execute auto-injected tool call: {e}")
                    injected_execution_result = None
            
            # Initialize tools_context to avoid UnboundLocalError
            system_prompt = None
            
            if is_greeting:
                # ⚡ MINIMAL GREETING (150 chars)
                system_prompt = f"You are Zoe, a friendly AI assistant. Respond warmly to the greeting in 5-10 words."
                logger.info(f"⚡ GENIUS (STREAMING): Minimal greeting prompt ({len(system_prompt)} chars)")
                
            elif is_action:
                # 🎯 MODEL-ADAPTIVE FUNCTION CALLING
                action_prompt = await get_model_adaptive_action_prompt(routing.get("model", "hermes3:8b-llama3.1-q4_K_M"))
                system_prompt = action_prompt
                logger.info(f"🎯 Using model-adaptive function calling prompt: {len(system_prompt)} chars")
                    
            else:
                # 💬 REGULAR CONVERSATION
                prompt_cache_key = f"conversation_{user_id_for_prompt}"
                system_prompt = await context_cache.get("system_prompt", prompt_cache_key)
                
                if not system_prompt:
                    system_prompt = await build_system_prompt(memories, user_context, "conversation", user_id_for_prompt)
                    await context_cache.set("system_prompt", prompt_cache_key, system_prompt, CACHE_TTL["system_prompt"])
                    logger.info(f"💬 Regular prompt (streaming): {len(system_prompt)} chars")
                else:
                    logger.info(f"✅ Cached conversation prompt (streaming)")
            
            # ✅ PHASE 2: MIGRATE TO /api/chat FOR PROPER PROMPT CACHING
            # Use conversation context management for KV cache reuse
            user_id_for_chat = context.get("user_id", "default")
            
            # Get conversation history from cache (for KV cache reuse)
            conversation_key = f"conversation_{user_id_for_chat}"
            conversation_history = await context_cache.get("conversation", conversation_key) or []
            
            # Build messages array (not single prompt) for proper KV caching
            messages = [
                {"role": "system", "content": system_prompt},  # Cached in KV cache
            ]
            
            # Add conversation history (last 3 messages for context)
            for msg_item in conversation_history[-3:]:
                messages.append(msg_item)
            
            # Add current user message
            messages.append({"role": "user", "content": message})
            
            # Update conversation history
            conversation_history.append({"role": "user", "content": message})
            # Keep only last 10 messages
            conversation_history = conversation_history[-10:]
            await context_cache.set("conversation", conversation_key, conversation_history, 3600)
            
            # Stream from LiteLLM gateway (handles all models: local + cloud)
            # LiteLLM provides: unified API, fallbacks, caching, load balancing
            llm_url = "http://zoe-litellm:8001/v1/chat/completions"
            
            # ✅ PRIMARY MODEL: gemma3n-e2b-gpu-fixed (5.6GB - now fits with proper memory management)
            # Keep loaded for 30m to avoid 20s reload penalty
            query_type = routing.get("type", "conversation")
            selected_model = model_selector.select_model(query_type)
            logger.info(f"🚀 Selected model: {selected_model} for query type: {query_type}")
            model_config = model_selector.get_model_config(selected_model)
            
            # ✅ VOICE FIX: Only increase for capability questions, keep voice responses concise
            # Voice mode should stay at 128 tokens, but capability questions need more detail
            if any(phrase in message.lower() for phrase in ["what can you", "what are your", "tell me what", "capabilities", "what things"]):
                # Temporarily increase num_predict for capability questions (NOT for voice mode)
                is_voice_mode = context.get('voice_mode', False)
                if not is_voice_mode:
                    original_num_predict = model_config.num_predict
                    model_config.num_predict = max(256, model_config.num_predict * 2)  # Reduced from 512 for faster responses
                    logger.info(f"📏 Increased response length to {model_config.num_predict} for capability question")
            
            logger.info(f"🤖 Streaming with model: {selected_model} (using /api/chat for KV cache)")
            
            # Check if TensorRT is available and model should use it
            use_tensorrt = False
            tensorrt_url = os.getenv("TENSORRT_URL", "http://zoe-tensorrt:8011")
            if "hermes3" in selected_model.lower():
                try:
                    async with httpx.AsyncClient(timeout=2.0) as trt_client:
                        trt_health = await trt_client.get(f"{tensorrt_url}/health")
                        if trt_health.status_code == 200 and trt_health.json().get("tensorrt_loaded"):
                            use_tensorrt = True
                            logger.info("🚀 Using TensorRT GPU acceleration")
                except:
                    pass
            
            # AG-UI Event: agent_state_delta (model selected)
            engine_type = "tensorrt-gpu" if use_tensorrt else "llm-inference"
            yield f"data: {json.dumps({'type': 'agent_state_delta', 'state': {'model': selected_model, 'engine': engine_type, 'status': 'generating'}, 'timestamp': datetime.now().isoformat()})}\n\n"
            
            # Check if this requires tool calls via MCP (for AG-UI events)
            if tools_context and routing.get("requires_tools"):
                # AG-UI Event: action (tool call)
                yield f"data: {json.dumps({'type': 'action', 'name': 'mcp_tools', 'arguments': {{'query': message}}, 'status': 'running', 'timestamp': datetime.now().isoformat()})}\n\n"
            
            async with httpx.AsyncClient(timeout=60.0) as client:  # Increased timeout for model loading
                # Route to TensorRT or LLM inference server
                if use_tensorrt:
                    # TensorRT endpoint - OpenAI compatible
                    endpoint_url = f"{tensorrt_url}/api/generate"
                    request_json = {
                        "prompt": messages[-1]["content"] if messages else message,
                        "max_tokens": model_config.num_predict,
                        "temperature": model_config.temperature,
                        "top_p": model_config.top_p,
                        "stream": False  # TensorRT doesn't support streaming yet
                    }
                else:
                    # LLM inference server endpoint (OpenAI-compatible format)
                    endpoint_url = llm_url
                    request_json = {
                        "model": selected_model,
                        "messages": messages,
                        "stream": True,
                        "temperature": model_config.temperature,
                        "top_p": model_config.top_p,
                        "max_tokens": model_config.num_predict,
                        "stop": model_config.stop_tokens if model_config.stop_tokens else []
                    }
                
                # LiteLLM requires authentication
                headers = {}
                if "litellm" in llm_url:
                    headers["Authorization"] = "Bearer sk-f3320300bb32df8f176495bb888ba7c8f87a0d01c2371b50f767b9ead154175f"
                
                async with client.stream(
                    "POST",
                    endpoint_url,
                    json=request_json,
                    headers=headers
                ) as response:
                    # Check response status
                    if response.status_code != 200:
                        try:
                            error_text = await response.aread()
                            error_msg = error_text.decode()[:500] if error_text else "Unknown error"
                            logger.error(f"LLM error {response.status_code} for {selected_model}: {error_msg}")
                            
                            # Try fallback model
                            fallback_model = model_selector.get_fallback_model(selected_model)
                            if fallback_model != selected_model and fallback_model in MODEL_CONFIGS:
                                logger.info(f"🔄 Retrying with fallback model: {fallback_model}")
                                fallback_config = model_selector.get_model_config(fallback_model)
                                fallback_num_gpu = 1 if "gemma3n-e2b-gpu-fixed" in fallback_model else 0
                                
                                async with httpx.AsyncClient(timeout=fallback_config.timeout) as fallback_client:
                                    async with fallback_client.stream(
                                        "POST",
                                        llm_url,
                                        json={
                                            "model": fallback_model,
                                            "messages": messages,  # Use same messages for fallback
                                            "stream": True,
                                            "temperature": fallback_config.temperature,
                                            "top_p": fallback_config.top_p,
                                            "max_tokens": fallback_config.num_predict,
                                            "stop": fallback_config.stop_tokens if fallback_config.stop_tokens else []
                                        },
                                        headers=headers  # Reuse headers from above (includes auth)
                                    ) as fallback_response:
                                        if fallback_response.status_code == 200:
                                            full_response = ""
                                            async for line in fallback_response.aiter_lines():
                                                if line.strip():
                                                    # Handle OpenAI SSE format
                                                    line_data = line.strip()
                                                    if line_data.startswith("data: "):
                                                        line_data = line_data[6:]
                                                    if line_data == "[DONE]":
                                                        continue
                                                    
                                                    try:
                                                        chunk = json.loads(line_data)
                                                        # OpenAI format: choices[0].delta.content
                                                        token = None
                                                        if "choices" in chunk and len(chunk["choices"]) > 0:
                                                            delta = chunk["choices"][0].get("delta", {})
                                                            token = delta.get("content", "")
                                                        # Fallback: Ollama format
                                                        elif "message" in chunk and "content" in chunk["message"]:
                                                            token = chunk["message"]["content"]
                                                        elif "response" in chunk:
                                                            token = chunk["response"]
                                                        
                                                        if token:
                                                            full_response += token
                                                            yield f"data: {json.dumps({'type': 'message_delta', 'delta': token, 'timestamp': datetime.now().isoformat()})}\n\n"
                                                    except Exception as e:
                                                        logger.error(f"Error parsing fallback chunk: {e}")
                                            # Continue with tool calls and session_end
                                        else:
                                            yield f"data: {json.dumps({'type': 'error', 'error': {'message': f'Both {selected_model} and {fallback_model} failed. Please check LLM service.', 'code': 'MODEL_ERROR'}, 'timestamp': datetime.now().isoformat()})}\n\n"
                                            return
                            else:
                                yield f"data: {json.dumps({'type': 'error', 'error': {'message': f'Model {selected_model} failed: {error_msg}', 'code': 'LLM_ERROR'}, 'timestamp': datetime.now().isoformat()})}\n\n"
                                return
                        except Exception as e:
                            logger.error(f"Error handling LLM failure: {e}")
                            yield f"data: {json.dumps({'type': 'error', 'error': {'message': f'LLM request failed: {str(e)}', 'code': 'LLM_ERROR'}, 'timestamp': datetime.now().isoformat()})}\n\n"
                            return
                    
                    full_response = ""
                    code_blocks_found = []
                    current_code_block = None
                    in_code_block = False
                    code_block_language = None
                    code_block_start_pos = None
                    in_tool_call = False
                    tool_call_buffer = ""
                    
                    async for line in response.aiter_lines():
                        if line.strip():
                            # Handle OpenAI SSE format: "data: {...}"
                            line_data = line.strip()
                            if line_data.startswith("data: "):
                                line_data = line_data[6:]  # Remove "data: " prefix
                            
                            # Skip [DONE] message
                            if line_data == "[DONE]":
                                continue
                            
                            try:
                                chunk = json.loads(line_data)
                                # OpenAI format: choices[0].delta.content
                                token = None
                                if "choices" in chunk and len(chunk["choices"]) > 0:
                                    delta = chunk["choices"][0].get("delta", {})
                                    token = delta.get("content", "")
                                # Fallback: Ollama format (message.content or response)
                                elif "message" in chunk and "content" in chunk["message"]:
                                    token = chunk["message"]["content"]
                                elif "response" in chunk:
                                    token = chunk["response"]
                                
                                if token:
                                    full_response += token
                                    
                                    # Detect code block markers during streaming
                                    if "```typescript" in token or "```python" in token:
                                        in_code_block = True
                                        code_block_language = "typescript" if "typescript" in token else "python"
                                        current_code_block = ""
                                        code_block_start_pos = len(full_response) - len(token)
                                        # Don't stream the opening code block marker
                                        continue
                                    elif in_code_block and "```" in token:
                                        # End of code block - execute it immediately
                                        if current_code_block:
                                            code_blocks_found.append((current_code_block.strip(), code_block_language))
                                            logger.info(f"🔧 Found code block during streaming, executing...")
                                            # Execute immediately
                                            exec_result = await execute_code(current_code_block.strip(), context.get("user_id", "default"), code_block_language)
                                            if exec_result.get("success"):
                                                output = exec_result.get("output", "")
                                                # Extract success message
                                                success_message = None
                                                try:
                                                    if "{" in output and "}" in output:
                                                        json_start = output.find("{")
                                                        json_end = output.rfind("}") + 1
                                                        json_str = output[json_start:json_end]
                                                        parsed = json.loads(json_str)
                                                        if isinstance(parsed, dict):
                                                            success_message = parsed.get("message") or parsed.get("success")
                                                except:
                                                    pass
                                                
                                                if not success_message:
                                                    if "✅" in output:
                                                        success_message = output.split("✅")[-1].strip()
                                                    elif output.strip():
                                                        lines = output.strip().split("\n")
                                                        for line in lines:
                                                            if line and not line.startswith("{") and len(line) < 200:
                                                                success_message = line.strip()
                                                                break
                                                
                                                if not success_message:
                                                    success_message = "Done! ✅"
                                                
                                                # Stream the success message instead of code
                                                yield f"data: {json.dumps({'type': 'message_delta', 'delta': f' {success_message}', 'timestamp': datetime.now().isoformat()})}\n\n"
                                                logger.info(f"✅ Code executed and result streamed: {success_message[:100]}")
                                            else:
                                                error = exec_result.get("error", "Unknown error")
                                                error_msg = f"Sorry, I encountered an error: {error[:200]}"
                                                yield f"data: {json.dumps({'type': 'message_delta', 'delta': f' {error_msg}', 'timestamp': datetime.now().isoformat()})}\n\n"
                                        in_code_block = False
                                        current_code_block = None
                                        code_block_start_pos = None
                                        # Don't stream the closing marker
                                        continue
                                    elif in_code_block:
                                        # Accumulate code block content (don't stream it)
                                        current_code_block += token
                                        continue
                                    
                                    # Check for tool calls during streaming (intercept before streaming)
                                    if "[TOOL_CALL:" in token:
                                        in_tool_call = True
                                        tool_call_buffer = token
                                        # Don't stream tool call tokens - accumulate them
                                        continue
                                    elif in_tool_call:
                                        # Accumulate tool call tokens until we find the closing bracket
                                        tool_call_buffer += token
                                        if "]" in token and tool_call_buffer.count("[") <= tool_call_buffer.count("]"):
                                            # Complete tool call found - execute it immediately
                                            in_tool_call = False
                                            logger.info(f"🔧 Found tool call during streaming: {tool_call_buffer[:100]}")
                                            # Execute the tool call
                                            try:
                                                executed_response = await parse_and_execute_code_or_tools(tool_call_buffer, context.get("user_id", "default"))
                                                if executed_response != tool_call_buffer:
                                                    # Tool was executed - stream the result instead
                                                    success_msg = executed_response.replace(tool_call_buffer, "").strip()
                                                    if not success_msg:
                                                        success_msg = "Done! ✅"
                                                    yield f"data: {json.dumps({'type': 'message_delta', 'delta': success_msg, 'timestamp': datetime.now().isoformat()})}\n\n"
                                                    logger.info(f"✅ Tool executed and result streamed: {success_msg[:100]}")
                                                else:
                                                    # Tool call wasn't parsed/executed - stream it as-is (fallback)
                                                    yield f"data: {json.dumps({'type': 'message_delta', 'delta': tool_call_buffer, 'timestamp': datetime.now().isoformat()})}\n\n"
                                            except Exception as e:
                                                logger.error(f"Error executing tool call during streaming: {e}")
                                                # Stream error message
                                                yield f"data: {json.dumps({'type': 'message_delta', 'delta': f'Sorry, I encountered an error executing that action.', 'timestamp': datetime.now().isoformat()})}\n\n"
                                            tool_call_buffer = ""
                                            continue
                                        else:
                                            # Still accumulating tool call
                                            continue
                                    
                                    # Stream normal content (not code blocks or tool calls)
                                    yield f"data: {json.dumps({'type': 'message_delta', 'delta': token, 'timestamp': datetime.now().isoformat()})}\n\n"
                                elif "error" in chunk:
                                    # LLM returned error in response
                                    error_msg = chunk.get("error", "Unknown error")
                                    logger.error(f"LLM response error: {error_msg}")
                                    yield f"data: {json.dumps({'type': 'error', 'error': {'message': str(error_msg), 'code': 'LLM_ERROR'}, 'timestamp': datetime.now().isoformat()})}\n\n"
                                    return
                            except Exception as e:
                                logger.error(f"Error parsing chunk: {e}, line: {line[:100]}")
                    
                    # Also check full_response for any code blocks or tool calls that might have been missed
                    if full_response:
                        original_response = full_response
                        tool_calls = await parse_and_execute_code_or_tools(full_response, context.get("user_id", "default"))
                        if tool_calls != original_response:
                            # Tool calls were executed - replace the tool call text with the result
                            # Find what was replaced and stream the replacement
                            if "[TOOL_CALL:" in original_response:
                                # Extract the success message from the replaced response
                                # The replacement should have removed [TOOL_CALL:...] and added success message
                                success_message = tool_calls.replace(original_response.split("[TOOL_CALL:")[0], "").strip()
                                if success_message and success_message != tool_calls:
                                    # Stream the success message
                                    yield f"data: {json.dumps({'type': 'message_delta', 'delta': success_message, 'timestamp': datetime.now().isoformat()})}\n\n"
                                else:
                                    # Fallback: stream the full replaced response
                                    diff = tool_calls[len(original_response):]
                                    if diff:
                                        yield f"data: {json.dumps({'type': 'message_delta', 'delta': diff, 'timestamp': datetime.now().isoformat()})}\n\n"
                            else:
                                # Code block execution - stream the diff
                                diff = tool_calls[len(original_response):]
                                if diff:
                                    yield f"data: {json.dumps({'type': 'message_delta', 'delta': diff, 'timestamp': datetime.now().isoformat()})}\n\n"
                            full_response = tool_calls
                            # AG-UI Event: action_result (tool execution completed)
                            yield f"data: {json.dumps({'type': 'action_result', 'result': {'executed': True, 'response': tool_calls}, 'timestamp': datetime.now().isoformat()})}\n\n"
                    
                    # Update conversation history with assistant response
                    conversation_history.append({"role": "assistant", "content": full_response})
                    conversation_history = conversation_history[-10:]  # Keep last 10 messages
                    await context_cache.set("conversation", conversation_key, conversation_history, 3600)
                    
                    # AG-UI Event: session_end
                    yield f"data: {json.dumps({'type': 'session_end', 'session_id': session_id, 'final_state': {'tokens': len(full_response), 'complete': True}, 'timestamp': datetime.now().isoformat()})}\n\n"
                    
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"Streaming error: {e}\n{error_details}")
            # AG-UI Event: error
            yield f"data: {json.dumps({'type': 'error', 'error': {'message': str(e) or 'Unknown streaming error', 'code': 'STREAM_ERROR'}, 'timestamp': datetime.now().isoformat()})}\n\n"
    
    return generate()

async def generate_response(message: str, context: Dict, memories: Dict, user_context: Dict, routing: Dict = None, model: str = None) -> str:
    """Generate response from LLM inference server with full context using flexible model selection"""
    routing_type = routing.get("type", "conversation") if routing else "conversation"
    user_id_for_prompt = context.get("user_id", "default")
    
    # Get model from routing or use default
    if not model:
        model = routing.get("model", "hermes3:8b-llama3.1-q4_K_M") if routing else "hermes3:8b-llama3.1-q4_K_M"
    
    # 🚀 GENIUS: ANTHROPIC-STYLE ADAPTIVE PROMPT SIZING
    # Insight from Anthropic engineering, phidata, OpenInterpreter: DON'T send 8KB for "hi"!
    # - Simple greetings: ~150 chars (50x smaller!)
    # - Regular queries: ~1.5KB (5x smaller)
    # - Actions: Full ~8KB+ (only when needed)
    
    is_greeting = len(message) < 20 and any(g in message.lower() for g in ["hi", "hello", "hey", "morning", "afternoon", "evening", "thanks", "thank you", "bye", "goodnight"])
    is_action = routing_type == "action" or routing.get("mcp_tools_needed", False)
    
    # 🎯 HYBRID APPROACH: Auto-inject tool calls for detected patterns (safety net)
    # This ensures 100% success even if LLM doesn't generate tool calls
    injected_tool_call = _auto_inject_tool_call(message)
    injected_execution_result = None  # Initialize for all code paths
    if injected_tool_call and is_action:
        logger.info(f"🎯 AUTO-INJECTED tool call for guaranteed execution: {injected_tool_call[:80]}...")
        # Execute the injected tool call immediately for 100% reliability
        # Don't wait for LLM to generate it (LLM might not include it)
        try:
            injected_response = await parse_and_execute_code_or_tools(injected_tool_call, user_id_for_prompt)
            logger.info(f"✅ Executed auto-injected tool call: {injected_response[:100]}")
            # Store the execution result to include in final response
            injected_execution_result = injected_response
            
            # ✅ FIX: If tool executed successfully, return result immediately (skip LLM)
            if injected_execution_result and "Error" not in injected_execution_result and "❌" not in injected_execution_result:
                logger.info(f"🎯 Returning auto-injected tool result immediately (skipping LLM)")
                return {
                    "response": injected_execution_result,
                    "response_time": time.time() - start_time,
                    "routing": "action_auto_injected",
                    "memories_used": 0,
                    "episode_id": episode_id
                }
        except Exception as e:
            logger.warning(f"❌ Failed to execute auto-injected tool call: {e}")
            injected_execution_result = None
    
    if is_greeting:
        # ⚡ MINIMAL GREETING (150 chars) - Inspired by Google Assistant's speed
        system_prompt = f"You are Zoe, a friendly AI assistant. Respond warmly to the greeting in 5-10 words. Focus on the USER - ask how THEY are or how you can help THEM. Do not talk about yourself, your day, or make up experiences. You're an AI, not a person with a personal life."
        logger.info(f"⚡ GENIUS: Anti-hallucination greeting prompt")
        
    elif is_action:
        # 🎯 MODEL-ADAPTIVE FUNCTION CALLING (non-streaming)
        action_prompt = await get_model_adaptive_action_prompt(model)
        system_prompt = action_prompt
        logger.info(f"🎯 Using {model}-adaptive function calling prompt (non-streaming): {len(system_prompt)} chars")
    
    else:
        # 💬 REGULAR CONVERSATION - Use minimal prompt to avoid example contamination
        system_prompt = """You are Zoe, a warm and helpful AI assistant.

CRITICAL RULES:
1. You are an AI assistant - do NOT pretend to have personal experiences, meals, friends, or days
2. Do NOT use first-person experiences (e.g., "I had dinner", "I met friends", "my day was")
3. Do NOT make up stories, events, or people
4. Do NOT prefix responses with "Zoe:", "Response:", or "Assistant:"
5. Focus on helping the USER - ask about THEIR life, not yours
6. If asked about yourself, briefly explain you're an AI assistant designed to help

Be helpful, friendly, and honest about being an AI."""
        logger.info(f"💬 Using anti-hallucination conversation prompt")
    
    full_prompt = f"{system_prompt}\n\nUser's message: {message}\nZoe:"
    
    # Select the best model based on routing and performance
    query_type = routing.get("type", "conversation") if routing else "conversation"
    selected_model = model_selector.select_model(query_type)
    model_config = model_selector.get_model_config(selected_model)
    
    logger.info(f"🤖 Using model: {selected_model} ({model_config.category.value}) for {query_type}")
    logger.info(f"⏱️ Timeout set to: {model_config.timeout} seconds")
    logger.info(f"📏 System prompt length: {len(system_prompt)} chars")
    
    try:
        # 🚀 USE PROVIDER ABSTRACTION (llama.cpp, vLLM, TGI, etc.)
        provider = get_llm_provider()
        logger.info(f"🔄 Sending request via {provider.__class__.__name__}")
        
        # Use provider abstraction for generation
        start_time = time.time()
        response_text = await provider.generate(
            prompt=full_prompt,
            model=selected_model,
            temperature=model_config.temperature,
            max_tokens=model_config.num_predict
        )
        response_time = time.time() - start_time
        logger.info(f"✅ Got response via provider: {len(response_text)} chars in {response_time:.2f}s")
        
        # Analyze response quality
        quality_scores = QualityAnalyzer.analyze_response(response_text, query_type)
        
        # Update performance metrics with quality data
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
        
        return clean_llm_response(response_text)
            
    except Exception as e:
        import traceback
        logger.error(f"LLM error with {selected_model}: {e}\n{traceback.format_exc()}")
        
        # Update performance metrics for failure
        model_selector.update_performance(selected_model, model_config.timeout, False)
        
        # Try fallback model
        fallback_model = model_selector.get_fallback_model(selected_model)
        if fallback_model != selected_model:
            logger.info(f"🔄 Trying fallback model: {fallback_model}")
            try:
                fallback_config = model_selector.get_model_config(fallback_model)
                
                # LiteLLM requires authentication
                headers = {}
                if "litellm" in llm_url:
                    headers["Authorization"] = "Bearer sk-f3320300bb32df8f176495bb888ba7c8f87a0d01c2371b50f767b9ead154175f"
                
                async with httpx.AsyncClient(timeout=fallback_config.timeout) as client:
                    response = await client.post(
                        llm_url,
                        json={
                            "model": fallback_model,
                            "messages": messages,  # ✅ Use messages array
                            "stream": False,
                            "options": {
                                "temperature": fallback_config.temperature,
                                "top_p": fallback_config.top_p,
                                "num_predict": fallback_config.num_predict,
                                "num_ctx": fallback_config.num_ctx,
                                "repeat_penalty": fallback_config.repeat_penalty,
                                "stop": fallback_config.stop_tokens,
                                "num_gpu": fallback_config.num_gpu if fallback_config.num_gpu is not None else 1  # ✅ MODEL-SPECIFIC GPU
                            }
                        },
                        headers=headers
                    )
                    data = response.json()
                    # ✅ /api/chat returns message.content
                    response_text = data.get("message", {}).get("content", "") or data.get("response", "I'm here to help!")
                    
                    # Update performance metrics for fallback
                    response_time = data.get("total_duration", 0) / 1e9
                    model_selector.update_performance(fallback_model, response_time, True)
                    
                    return clean_llm_response(response_text)
                    
            except Exception as fallback_error:
                logger.error(f"Fallback model {fallback_model} also failed: {fallback_error}")
                model_selector.update_performance(fallback_model, fallback_config.timeout, False)
        
        return "Hi there! 😊 I'm here and ready to help. How can I assist you today?"

# ============================================================================
# MCP SERVER INTEGRATION - PROPER TOOL CONTEXT
# ============================================================================

async def get_model_adaptive_action_prompt(model_name: str) -> str:
    """
    Get action prompt ADAPTED to the specific model's strengths.
    ALL models output the SAME format: [TOOL_CALL:tool_name:{"params"}]
    But INSTRUCTIONS vary based on model capabilities.
    """
    
    # Common output format (same for ALL models)
    output_format = """
OUTPUT FORMAT (REQUIRED):
[TOOL_CALL:tool_name:{"param1":"value1","param2":"value2"}]

Example: [TOOL_CALL:add_to_list:{"list_name":"shopping","task_text":"bread","priority":"medium"}]
"""
    
    # Model-specific instructions
    if "hermes" in model_name.lower():
        # Hermes-3: Supports native function calling, use structured approach
        prompt = f"""You are Zoe. You have access to functions for taking actions.

{output_format}

AVAILABLE FUNCTIONS:
- add_to_list(list_name, task_text, priority) - Add item to a list
- create_calendar_event(title, start_date, start_time) - Schedule an event
- get_calendar_events() - Retrieve upcoming events
- create_person(name, relationship) - Add a person to your network
- search_memories(query) - Search your memories
- store_self_fact(fact_key, fact_value) - Store facts about the USER (e.g., "My favorite food is pizza")
- get_self_info(fact_key) - Retrieve facts about the USER (e.g., "What is my favorite food?")

INSTRUCTIONS:
1. Detect user intent (e.g., "add X" = call add_to_list)
2. Generate [TOOL_CALL:...] on first line
3. Add friendly confirmation after

Example:
User: "add bread to shopping"
You: [TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"bread","priority":"medium"}}]
Added bread to your shopping list! ✓
"""
    
    elif "qwen" in model_name.lower():
        # Qwen: Uses Hermes-style tool calling with XML tags (not [TOOL_CALL:...] format!)
        # Based on Qwen documentation: https://qwen.readthedocs.io/
        prompt = f"""You are Zoe, a helpful AI assistant with access to tools.

# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{{"type": "function", "function": {{"name": "add_to_list", "description": "Add an item to a shopping or todo list", "parameters": {{"type": "object", "properties": {{"list_name": {{"type": "string", "description": "Name of the list (e.g., 'shopping', 'todo')"}}, "task_text": {{"type": "string", "description": "The item to add"}}, "priority": {{"type": "string", "enum": ["low", "medium", "high"], "description": "Priority level"}}}}, "required": ["list_name", "task_text"]}}}}}}
{{"type": "function", "function": {{"name": "create_calendar_event", "description": "Create a calendar event", "parameters": {{"type": "object", "properties": {{"title": {{"type": "string", "description": "Event title"}}, "start_date": {{"type": "string", "description": "Date in YYYY-MM-DD format"}}, "start_time": {{"type": "string", "description": "Time in HH:MM format"}}}}, "required": ["title", "start_date"]}}}}}}
{{"type": "function", "function": {{"name": "search_memories", "description": "Search through memories and knowledge", "parameters": {{"type": "object", "properties": {{"query": {{"type": "string", "description": "Search query"}}}}, "required": ["query"]}}}}}}
{{"type": "function", "function": {{"name": "store_self_fact", "description": "Store a fact about the USER themselves (e.g., My favorite food is pizza)", "parameters": {{"type": "object", "properties": {{"fact_key": {{"type": "string", "description": "Category like favorite_food, birthday, hobby"}}, "fact_value": {{"type": "string", "description": "The fact value"}}}}, "required": ["fact_key", "fact_value"]}}}}}}
{{"type": "function", "function": {{"name": "get_self_info", "description": "Get information about the USER themselves", "parameters": {{"type": "object", "properties": {{"fact_key": {{"type": "string", "description": "Specific fact to retrieve (optional)"}}}}, "required": []}}}}}}
</tools>

For each function call, return a JSON object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{{"name": "function_name", "arguments": {{"param1": "value1", "param2": "value2"}}}}
</tool_call>

# Examples

User: "Add milk to my shopping list"
Assistant: <tool_call>
{{"name": "add_to_list", "arguments": {{"list_name": "shopping", "task_text": "milk", "priority": "medium"}}}}
</tool_call>

User: "Schedule dentist tomorrow at 2pm"
Assistant: <tool_call>
{{"name": "create_calendar_event", "arguments": {{"title": "dentist", "start_date": "2025-11-11", "start_time": "14:00"}}}}
</tool_call>

User: "I need to buy bread"
Assistant: <tool_call>
{{"name": "add_to_list", "arguments": {{"list_name": "shopping", "task_text": "bread", "priority": "medium"}}}}
</tool_call>

User: "My favorite food is pizza"
Assistant: <tool_call>
{{"name": "store_self_fact", "arguments": {{"fact_key": "favorite_food", "fact_value": "pizza"}}}}
</tool_call>

User: "What is my favorite food?"
Assistant: <tool_call>
{{"name": "get_self_info", "arguments": {{"fact_key": "favorite_food"}}}}
</tool_call>

CRITICAL: When user requests an action (add, create, schedule, buy, etc.), you MUST respond with a <tool_call> tag first, then add friendly text after.
CRITICAL: When user says "My X is Y", use store_self_fact to remember it about the USER.
"""
    
    elif "gemma" in model_name.lower():
        # Gemma: Needs MORE examples and explicit patterns
        prompt = f"""You are Zoe. When user wants to ADD, CREATE, or SCHEDULE something, you MUST call a function.

{output_format}

PATTERN MATCHING:
If user says "add [ITEM]" → [TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"[ITEM]","priority":"medium"}}]
If user says "schedule [EVENT]" → [TOOL_CALL:create_calendar_event:{{"title":"[EVENT]","start_date":"YYYY-MM-DD","start_time":"HH:MM"}}]

EXAMPLES (COPY THIS PATTERN):
1. User: "add bananas to shopping"
   You: [TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"bananas","priority":"medium"}}]
   Done!

2. User: "add dentist appointment tomorrow 2pm"
   You: [TOOL_CALL:create_calendar_event:{{"title":"dentist appointment","start_date":"2025-11-10","start_time":"14:00"}}]
   Scheduled!

CRITICAL: Your FIRST characters must be [TOOL_CALL: for action requests!
"""
    
    else:
        # Default: Works for Phi, Llama, and other models
        prompt = f"""You are Zoe. For action requests, use this EXACT format:

{output_format}

TOOLS AVAILABLE:
- add_to_list(list_name, task_text, priority)
- create_calendar_event(title, start_date, start_time)
- create_person(name, relationship)

MANDATORY EXAMPLES:
User: "add eggs"
You: [TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"eggs","priority":"medium"}}]
Added!

User: "schedule lunch tomorrow"
You: [TOOL_CALL:create_calendar_event:{{"title":"lunch","start_date":"2025-11-10","start_time":"12:00"}}]
Scheduled!

RULE: Action = MUST start with [TOOL_CALL:...]
"""
    
    logger.info(f"📋 Generated {model_name}-specific action prompt ({len(prompt)} chars)")
    return prompt

async def get_mcp_tools_context() -> str:
    """Get MCP tools context using code execution pattern (progressive disclosure) with caching"""
    # Check cache first
    cache_key = "mcp_tools_context"
    cached = await context_cache.get("mcp_tools", cache_key)
    if cached:
        logger.debug("✅ Using cached MCP tools context")
        return cached
    
    try:
        import httpx
        
        async with httpx.AsyncClient() as client:
            # Get available tools from MCP server for search functionality
            response = await client.post(
                "http://zoe-mcp-server:8003/tools/list",
                json={"_auth_token": "default", "_session_id": "default"},
                timeout=2.0  # ✅ OPTIMIZED: Reduced timeout for faster fallback
            )
            
            if response.status_code == 200:
                tools_data = response.json()
                tools = tools_data.get("tools", [])
                
                # Group tools by category for better organization
                categories = {
                    "zoe-memory": [],
                    "zoe-lists": [],
                    "zoe-calendar": [],
                    "home-assistant": [],
                    "n8n": [],
                    "matrix": [],
                    "developer": []
                }
                
                for tool in tools:
                    name = tool.get("name", "")
                    if "memory" in name or "person" in name or "collection" in name:
                        categories["zoe-memory"].append(name)
                    elif "list" in name:
                        categories["zoe-lists"].append(name)
                    elif "calendar" in name or "event" in name:
                        categories["zoe-calendar"].append(name)
                    elif "home_assistant" in name or "ha" in name.lower():
                        categories["home-assistant"].append(name)
                    elif "n8n" in name or "workflow" in name:
                        categories["n8n"].append(name)
                    elif "matrix" in name:
                        categories["matrix"].append(name)
                    elif "developer" in name:
                        categories["developer"].append(name)
                
                # Code execution pattern - progressive disclosure
                tools_context = """
# MCP TOOLS VIA CODE EXECUTION (Progressive Disclosure Pattern)

You have access to MCP tools through code execution. This is more efficient than loading all tool definitions upfront.

## Available Tool Categories:
"""
                for category, tool_names in categories.items():
                    if tool_names:
                        tools_context += f"- **{category.replace('-', ' ').title()}**: {len(tool_names)} tools\n"
                
                tools_context += """
## How to Use Tools (Code Execution Pattern):

Instead of direct tool calls, write TypeScript code that imports and uses tools:

```typescript
// Example: Add item to shopping list
import * as zoeLists from './servers/zoe-lists';
const result = await zoeLists.addToList({
    list_name: 'shopping',
    task_text: 'bread',
    priority: 'medium'
});
console.log(`✅ ${result.message}`);
```

## Progressive Disclosure:

1. **Search for tools**: Use the search_tools function to find relevant tools
   - Example: search_tools("shopping list") → finds add_to_list, get_lists
   
2. **Load only needed tools**: Import only the tools you need for the current task
   - Example: import * as zoeLists from './servers/zoe-lists'

3. **Process data efficiently**: Filter/transform data in code before returning to user
   - Example: Filter 1000 events → return only 5 important ones

## Benefits:
- 98% fewer tokens (load only needed tools)
- Faster responses (smaller context window)
- Better privacy (data processed in execution environment)
- More powerful (loops, conditionals, error handling)

## Tool Categories Available:
"""
                for category, tool_names in categories.items():
                    if tool_names:
                        tools_context += f"\n### {category.replace('-', ' ').title()}\n"
                        for tool_name in tool_names[:5]:  # Show first 5
                            tools_context += f"- `{tool_name}`\n"
                        if len(tool_names) > 5:
                            tools_context += f"- ... and {len(tool_names) - 5} more\n"
                
                tools_context += """
## When User Asks "What Can You Do?":
- Mention you use code execution for efficiency
- List the main categories (Memory, Lists, Calendar, Home Assistant, N8N, Matrix)
- Give examples of what you can help with
- Explain that you can combine multiple tools in code for complex tasks
"""
                
                # Cache the result
                await context_cache.set("mcp_tools", cache_key, tools_context, CACHE_TTL["mcp_tools"])
                return tools_context
            else:
                fallback_context = """
# MCP TOOLS VIA CODE EXECUTION

You have access to MCP tools through code execution. Use the search_tools function to discover available tools, then write TypeScript code to use them.
"""
                # Cache fallback too (shorter TTL)
                await context_cache.set("mcp_tools", cache_key, fallback_context, 60)
                return fallback_context
                
    except Exception as e:
        logger.warning(f"Could not fetch MCP tools context: {e}")
        fallback_context = """
# MCP TOOLS VIA CODE EXECUTION

You have access to MCP tools through code execution. Use the search_tools function to discover available tools.
"""
        return fallback_context

async def search_tools(query: str, detail_level: str = "summary") -> str:
    """Search for relevant MCP tools (progressive disclosure)"""
    try:
        import httpx
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://zoe-mcp-server:8003/tools/list",
                json={"_auth_token": "default", "_session_id": "default"},
                timeout=5.0
            )
            
            if response.status_code == 200:
                tools_data = response.json()
                tools = tools_data.get("tools", [])
                
                # Simple keyword matching with better logic
                query_lower = query.lower()
                query_words = query_lower.split()
                matches = []
                
                for tool in tools:
                    name = tool.get("name", "")
                    description = tool.get("description", "")
                    
                    # Check if any query word matches tool name or description
                    name_lower = name.lower()
                    desc_lower = description.lower()
                    
                    # Match if query words appear in name or description
                    if any(word in name_lower or word in desc_lower for word in query_words):
                        matches.append(tool)
                    # Also check if tool name contains query (for "list" matching "add_to_list")
                    elif query_lower in name_lower.replace("_", " "):
                        matches.append(tool)
                
                if not matches:
                    return f"No tools found matching '{query}'"
                
                result = f"Found {len(matches)} tool(s) matching '{query}':\n\n"
                
                for tool in matches[:10]:  # Limit to 10 results
                    name = tool.get("name", "")
                    description = tool.get("description", "")
                    
                    if detail_level == "name":
                        result += f"- {name}\n"
                    elif detail_level == "summary":
                        result += f"- **{name}**: {description}\n"
                    else:  # full
                        result += f"- **{name}**: {description}\n"
                        result += f"  Import: `import * as category from './servers/category'`\n"
                
                return result
            else:
                return f"Error searching tools: {response.status_code}"
                
    except Exception as e:
        logger.error(f"Error searching tools: {e}")
        return f"Error searching tools: {str(e)}"

async def execute_code(code: str, user_id: str, language: str = "typescript") -> dict:
    """Execute code in secure sandbox - optimized for speed"""
    try:
        import httpx
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://zoe-code-execution:8010/execute",
                json={
                    "code": code,
                    "language": language,
                    "user_id": user_id,
                    "timeout": 10  # Reduced from 30 to 10 seconds for faster responses
                },
                timeout=12.0  # Reduced from 35 to 12 seconds
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "success": False,
                    "output": "",
                    "error": f"Code execution failed: {response.status_code}"
                }
                
    except httpx.TimeoutException:
        logger.warning("⏱️ Code execution timeout")
        return {
            "success": False,
            "output": "",
            "error": "Code execution timed out"
        }
    except Exception as e:
        logger.error(f"Error executing code: {e}")
        return {
            "success": False,
            "output": "",
            "error": str(e)
        }

async def parse_and_execute_code_or_tools(response_text: str, user_id: str) -> str:
    """Parse code blocks or tool calls from LLM response and execute them"""
    import re
    import json
    
    # Check for TypeScript code blocks first (code execution pattern)
    typescript_pattern = r'```typescript\n(.*?)```'
    python_pattern = r'```python\n(.*?)```'
    
    code_matches = re.findall(typescript_pattern, response_text, re.DOTALL)
    if not code_matches:
        code_matches = re.findall(python_pattern, response_text, re.DOTALL)
    
    if code_matches:
        logger.info(f"🔧 Found {len(code_matches)} code block(s) to execute")
        
        final_response = response_text
        
        for code_block in code_matches:
            code = code_block.strip()
            if not code:
                continue
            
            # Determine language
            language = "typescript" if "typescript" in response_text.lower() else "python"
            
            # Execute code with optimized timeout for simple actions
            result = await execute_code(code, user_id, language)
            
            if result.get("success"):
                output = result.get("output", "")
                
                # Try to extract a clean success message from the output
                # Look for JSON with success message, or console.log output
                success_message = None
                try:
                    # Try to parse JSON from output
                    if "{" in output and "}" in output:
                        json_start = output.find("{")
                        json_end = output.rfind("}") + 1
                        json_str = output[json_start:json_end]
                        parsed = json.loads(json_str)
                        if isinstance(parsed, dict):
                            success_message = parsed.get("message") or parsed.get("success")
                except:
                    pass
                
                # If no JSON message, look for console.log output
                if not success_message:
                    # Extract text after console.log or just use the output
                    if "✅" in output:
                        success_message = output.split("✅")[-1].strip()
                    elif output.strip():
                        # Try to extract meaningful message
                        lines = output.strip().split("\n")
                        for line in lines:
                            if line and not line.startswith("{") and len(line) < 200:
                                success_message = line.strip()
                                break
                
                # Default message if nothing found
                if not success_message:
                    success_message = "Done! ✅"
                
                # Replace the entire code block with just the success message
                code_block_marker = f"```{language}\n{code_block}```"
                # Remove code block and replace with clean message (remove any text after code block too)
                # Find text before and after code block
                before_code = final_response[:final_response.find(code_block_marker)]
                after_code = final_response[final_response.find(code_block_marker) + len(code_block_marker):]
                # Remove any duplicate confirmation messages
                if "Added" in after_code or "added" in after_code.lower():
                    # Keep only the success message, remove duplicate
                    final_response = before_code + success_message
                else:
                    final_response = before_code + success_message + after_code
                logger.info(f"✅ Code executed successfully: {success_message[:100]}")
            else:
                error = result.get("error", "Unknown error")
                # Replace code block with error message (but hide the code)
                code_block_marker = f"```{language}\n{code_block}```"
                error_message = f"Sorry, I encountered an error: {error[:200]}"
                final_response = final_response.replace(code_block_marker, error_message, 1)
                logger.warning(f"❌ Code execution failed: {error}")
        
        return final_response
    
    # Fallback to old tool call pattern for backward compatibility (FAST PATH for simple actions)
    return await parse_and_execute_tool_calls(response_text, user_id)

async def parse_and_execute_tool_calls(response_text: str, user_id: str) -> str:
    """Parse tool calls from LLM response and execute them (supports both old and Hermes-style formats)"""
    import re
    import json
    
    matches = []
    
    # 🎯 NEW: Parse Hermes-style XML format first (for Qwen models)
    # Format: <tool_call>{"name": "tool_name", "arguments": {...}}</tool_call>
    hermes_pattern = r'<tool_call>\s*(\{[^<]+\})\s*</tool_call>'
    hermes_matches = re.findall(hermes_pattern, response_text, re.DOTALL)
    
    for json_str in hermes_matches:
        try:
            # Clean up the JSON
            json_str_clean = json_str.strip()
            tool_data = json.loads(json_str_clean)
            tool_name = tool_data.get("name", "")
            arguments = tool_data.get("arguments", {})
            
            if tool_name and arguments:
                # Convert back to JSON string for consistency with old format
                params_json = json.dumps(arguments)
                matches.append((tool_name, params_json))
                logger.info(f"🎯 Parsed Hermes-style tool call: {tool_name}")
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse Hermes tool call: {json_str[:100]} (error: {e})")
    
    # 🔧 OLD: Parse legacy [TOOL_CALL:...] format (for backward compatibility)
    # Pattern to match tool calls: [TOOL_CALL:tool_name:{"param":"value"}]
    simple_pattern = r'\[TOOL_CALL:([^:]+):(\{.*?\})\]'
    
    # First try the simple pattern
    simple_matches = re.findall(simple_pattern, response_text, re.DOTALL)
    
    # For each match, try to parse the JSON to validate it
    for tool_name, params_json in simple_matches:
        try:
            # ✅ FIX: More aggressive quote fixing for curly quotes
            params_json_fixed = params_json.replace('"', '"').replace('"', '"').replace(''', "'").replace(''', "'")
            params_json_fixed = params_json_fixed.replace('\u201c', '"').replace('\u201d', '"')  # Unicode curly quotes
            params_json_fixed = params_json_fixed.replace('\u2018', "'").replace('\u2019', "'")  # Unicode curly single quotes
            # Try to parse to validate JSON
            json.loads(params_json_fixed)
            matches.append((tool_name, params_json_fixed))
        except json.JSONDecodeError:
            # If simple parse fails, try to find balanced braces
            # Find the position of the tool call
            start_pos = response_text.find(f"[TOOL_CALL:{tool_name}:")
            if start_pos != -1:
                # Find the opening brace after the colon
                brace_start = response_text.find('{', start_pos)
                if brace_start != -1:
                    # Count braces to find the matching closing brace
                    brace_count = 0
                    brace_end = brace_start
                    for i in range(brace_start, len(response_text)):
                        if response_text[i] == '{':
                            brace_count += 1
                        elif response_text[i] == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                brace_end = i
                                break
                    if brace_end > brace_start:
                        params_json = response_text[brace_start:brace_end + 1]
                        # ✅ FIX: More aggressive quote fixing
                        params_json_fixed = params_json.replace('"', '"').replace('"', '"').replace(''', "'").replace(''', "'")
                        params_json_fixed = params_json_fixed.replace('\u201c', '"').replace('\u201d', '"')  # Unicode curly quotes
                        params_json_fixed = params_json_fixed.replace('\u2018', "'").replace('\u2019', "'")  # Unicode curly single quotes
                        try:
                            json.loads(params_json_fixed)
                            matches.append((tool_name, params_json_fixed))
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse JSON for tool {tool_name}: {params_json[:100]} (error: {e})")
    
    if not matches:
        # Log if we see TOOL_CALL but couldn't parse it
        if "TOOL_CALL" in response_text:
            logger.warning(f"⚠️ Found TOOL_CALL in response but couldn't parse: {response_text[:500]}")
        return response_text  # No tool calls found, return original response
    
    logger.info(f"🔧 Found {len(matches)} tool call(s) to execute: {[m[0] for m in matches]}")
    
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
                
                # ✨ NEW: Generate intelligent suggestions
                try:
                    from suggestion_engine import suggestion_engine
                    
                    suggestions_data = await suggestion_engine.generate_post_action_suggestions(
                        tool_name=tool_name,
                        params=params,
                        result=result,
                        user_id=user_id,
                        context={"message": response_text, "session_id": "mcp_execution"}
                    )
                    
                    # Append suggestions to response
                    if suggestions_data.get("suggestions"):
                        success_msg += "\n\n"
                        for suggestion in suggestions_data["suggestions"]:
                            icon = {"related_item": "💡", "action_trigger": "📅", 
                                   "reminder": "⏰", "proactive": "✨", 
                                   "frequent_together": "💡", "preparation": "📋",
                                   "related": "🔗", "relationship_maintenance": "💝",
                                   "important_date": "🎂", "convert_to_task": "✅",
                                   "follow_up": "📌", "automation": "🤖"}.get(suggestion["type"], "💡")
                            success_msg += f"{icon} {suggestion['action']}\n"
                    
                    # Add alternatives if available
                    if suggestions_data.get("alternatives"):
                        success_msg += "\n💭 **Better approach:**\n"
                        for alt in suggestions_data["alternatives"]:
                            success_msg += f"   • {alt['suggestion']} - {alt['why']}\n"
                    
                    # Add insights if available
                    if suggestions_data.get("insights"):
                        success_msg += "\n📊 " + " ".join(suggestions_data["insights"])
                    
                    logger.info(f"✨ Generated {len(suggestions_data.get('suggestions', []))} suggestions for {tool_name}")
                    
                    # Log suggestions shown for acceptance tracking
                    if suggestions_data.get("suggestions"):
                        await suggestion_engine.log_suggestions_shown(
                            user_id,
                            tool_name,
                            suggestions_data["suggestions"]
                        )
                
                except Exception as e:
                    logger.warning(f"Failed to generate suggestions: {e}")
                
                # ✅ NEW: Try to replace Hermes-style XML tool calls first
                hermes_pattern = r'<tool_call>\s*\{[^}]*"name"\s*:\s*"' + re.escape(tool_name) + r'"[^<]*\}\s*</tool_call>'
                if re.search(hermes_pattern, final_response, re.DOTALL):
                    final_response = re.sub(hermes_pattern, success_msg, final_response, count=1, flags=re.DOTALL)
                    logger.info(f"✅ Replaced Hermes XML tool call with: {success_msg[:50]}")
                else:
                    # ✅ FIX: Try multiple replacement patterns to ensure we catch all variations
                    original_tool_call = f"[TOOL_CALL:{tool_name}:{params_json}]"
                    # Also try with the original params_json (before quote fixing)
                    original_params = response_text[response_text.find(f"[TOOL_CALL:{tool_name}:") + len(f"[TOOL_CALL:{tool_name}:"):response_text.find("]", response_text.find(f"[TOOL_CALL:{tool_name}:"))]
                    original_tool_call_variants = [
                        original_tool_call,
                        f"[TOOL_CALL:{tool_name}:{original_params}]",
                        f"[TOOL_CALL:{tool_name}:{params_json}]",
                    ]
                    for variant in original_tool_call_variants:
                        if variant in final_response:
                            final_response = final_response.replace(variant, success_msg, 1)
                            logger.info(f"✅ Replaced tool call variant with: {success_msg[:50]}")
                            break
                    else:
                        # If no exact match, try regex replacement
                        import re
                        tool_call_pattern = re.escape(f"[TOOL_CALL:{tool_name}:") + r".*?" + re.escape("]")
                        final_response = re.sub(tool_call_pattern, success_msg, final_response, count=1)
                        logger.info(f"✅ Replaced tool call via regex with: {success_msg[:50]}")
                
                # ✅ NEW: Learn from successful action patterns
                try:
                    await training_collector.log_action_pattern(
                        user_id=user_id,
                        tool_name=tool_name,
                        params=params,
                        success=True
                    )
                    logger.debug(f"📚 Learned action pattern: {tool_name} with params {params}")
                except Exception as e:
                    logger.warning(f"Failed to log action pattern: {e}")
            else:
                # Replace with error message
                error_msg = result.get("error", f"Failed to execute {tool_name}")
                
                # Try Hermes-style XML first
                hermes_pattern = r'<tool_call>\s*\{[^}]*"name"\s*:\s*"' + re.escape(tool_name) + r'"[^<]*\}\s*</tool_call>'
                if re.search(hermes_pattern, final_response, re.DOTALL):
                    final_response = re.sub(hermes_pattern, f"Error: {error_msg}", final_response, count=1, flags=re.DOTALL)
                else:
                    final_response = final_response.replace(
                        f"[TOOL_CALL:{tool_name}:{params_json}]",
                        f"Error: {error_msg}"
                    )
                
                # ✅ NEW: Learn from failed action patterns
                try:
                    await training_collector.log_action_pattern(
                        user_id=user_id,
                        tool_name=tool_name,
                        params=params,
                        success=False
                    )
                    logger.debug(f"📚 Learned failed action pattern: {tool_name}")
                except Exception as e:
                    logger.warning(f"Failed to log failed action pattern: {e}")
                
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
        
        # Initialize routing to None (will be set by Enhanced MEM Agent or intelligent_routing)
        routing = None
        
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
        
        # Step 0: 🎯 INTENT-FIRST CLASSIFICATION (Tier 0/1 - HassIL/Keywords)
        # Try intent classification before LLM routing (for both streaming AND non-streaming)
        if USE_INTENT_SYSTEM and intent_classifier and intent_executor:
            try:
                start_intent_time = time.time()
                intent = intent_classifier.classify(msg.message)
                
                if intent and intent.confidence >= 0.7:
                    # Intent matched! Execute directly
                    intent_latency_ms = (time.time() - start_intent_time) * 1000
                    stream_mode = "stream" if stream else "non-stream"
                    logger.info(
                        f"🎯 INTENT MATCH ({stream_mode}): {intent.name}, "
                        f"tier: {intent.tier}, "
                        f"confidence: {intent.confidence:.2f}, "
                        f"latency: {intent_latency_ms:.2f}ms"
                    )
                    
                    # P0-3: Get appropriate temperature for this intent
                    if FeatureFlags.USE_DYNAMIC_TEMPERATURE:
                        temperature = TemperatureManager.get_temperature_for_intent(intent)
                        logger.info(f"[P0-3] Temperature for {intent.name}: {temperature}")
                    
                    result = await intent_executor.execute(intent, actual_user_id, context.get("session_id", "default"))
                    
                    # P0-2: Add confidence formatting if enabled
                    response_text = result.message
                    if FeatureFlags.USE_CONFIDENCE_FORMATTING:
                        confidence = ResponseFormatter.estimate_response_confidence(response_text, intent, context_present=True)
                        response_text = ResponseFormatter.format_with_confidence(response_text, confidence)
                        logger.info(f"[P0-2] Confidence formatting applied: {confidence:.2f}")
                    
                    # ✨ Extract params BEFORE anything else (for logging and suggestions)
                    intent_to_tool = {
                        "ListAdd": "add_to_list",
                        "CalendarCreate": "create_calendar_event",
                        "CalendarAdd": "create_calendar_event",
                        "EventCreate": "create_calendar_event",
                        "ScheduleEvent": "create_calendar_event",
                        "PersonAdd": "add_person",
                        "NoteCreate": "create_note",
                        "CreateNote": "create_note",
                        "TaskAdd": "add_to_list",
                        "ReminderCreate": "create_reminder",
                    }
                    
                    tool_name = intent_to_tool.get(intent.name)
                    extracted_params = {}
                    
                    if tool_name and intent.name == "ListAdd":
                        # Extract item from message for ListAdd
                        import re
                        match = re.search(r'add\s+(.+?)\s+to\s+(?:the\s+)?(\w+)\s+list', msg.message, re.IGNORECASE)
                        if match:
                            extracted_params = {
                                "item": match.group(1).strip(),
                                "list_type": match.group(2).lower() if match.group(2) else "shopping"
                            }
                        else:
                            # Fallback: extract item name
                            words = msg.message.lower().replace("add", "").replace("to", "").replace("shopping", "").replace("list", "").replace("the", "").strip()
                            extracted_params = {"item": words, "list_type": "shopping"}
                        
                        # Log to action_logs immediately for learning
                        try:
                            import sqlite3
                            import json
                            conn = sqlite3.connect("/app/data/zoe.db")
                            cursor = conn.cursor()
                            cursor.execute("""
                                INSERT INTO action_logs (user_id, tool_name, tool_params, success, context)
                                VALUES (?, ?, ?, ?, ?)
                            """, (actual_user_id, "add_to_list", json.dumps(extracted_params), True, json.dumps({"message": msg.message})))
                            conn.commit()
                            conn.close()
                            logger.debug(f"📝 Logged action to action_logs: {extracted_params}")
                        except Exception as e:
                            logger.warning(f"Failed to log action: {e}")
                    
                    elif tool_name and intent.name in ["CalendarCreate", "CalendarAdd", "EventCreate", "ScheduleEvent"]:
                        # Extract calendar params from message
                        import re
                        # Try to extract event title
                        title_match = re.search(r'schedule\s+(?:a\s+)?(.+?)\s+for', msg.message, re.IGNORECASE)
                        if not title_match:
                            title_match = re.search(r'schedule\s+(?:a\s+)?(.+)', msg.message, re.IGNORECASE)
                        
                        extracted_params = {
                            "title": title_match.group(1).strip() if title_match else "Event",
                            "message_text": msg.message
                        }
                        
                        # Log to action_logs for learning
                        try:
                            import sqlite3
                            import json
                            conn = sqlite3.connect("/app/data/zoe.db")
                            cursor = conn.cursor()
                            cursor.execute("""
                                INSERT INTO action_logs (user_id, tool_name, tool_params, success, context)
                                VALUES (?, ?, ?, ?, ?)
                            """, (actual_user_id, "create_calendar_event", json.dumps(extracted_params), True, json.dumps({"message": msg.message})))
                            conn.commit()
                            conn.close()
                            logger.debug(f"📝 Logged calendar action: {extracted_params}")
                        except Exception as e:
                            logger.warning(f"Failed to log calendar action: {e}")
                    
                    # ✨ NEW: Generate intelligent suggestions after intent execution
                    if result.success:
                        try:
                            from suggestion_engine import suggestion_engine
                            
                            if tool_name:
                                # Use extracted params
                                params = extracted_params if extracted_params else (getattr(result, 'params', {}) or {})
                                
                                suggestions_data = await suggestion_engine.generate_post_action_suggestions(
                                    tool_name=tool_name,
                                    params=params,
                                    result={"success": True, "message": response_text},
                                    user_id=actual_user_id,
                                    context={"message": msg.message, "session_id": context.get("session_id", "default")}
                                )
                                
                                # Append suggestions to response
                                if suggestions_data.get("suggestions"):
                                    response_text += "\n\n"
                                    for suggestion in suggestions_data["suggestions"]:
                                        icon = {"related_item": "💡", "action_trigger": "📅", 
                                               "reminder": "⏰", "proactive": "✨", 
                                               "frequent_together": "💡", "preparation": "📋",
                                               "related": "🔗", "relationship_maintenance": "💝",
                                               "important_date": "🎂", "convert_to_task": "✅",
                                               "follow_up": "📌", "automation": "🤖"}.get(suggestion["type"], "💡")
                                        response_text += f"{icon} {suggestion['action']}\n"
                                
                                # Add alternatives if available
                                if suggestions_data.get("alternatives"):
                                    response_text += "\n💭 **Better approach:**\n"
                                    for alt in suggestions_data["alternatives"]:
                                        response_text += f"   • {alt['suggestion']} - {alt['why']}\n"
                                
                                # Add insights if available
                                if suggestions_data.get("insights"):
                                    response_text += "\n📊 " + " ".join(suggestions_data["insights"])
                                
                                logger.info(f"✨ Generated {len(suggestions_data.get('suggestions', []))} suggestions for intent {intent.name}")
                                
                                # Log suggestions shown for acceptance tracking
                                if suggestions_data.get("suggestions"):
                                    await suggestion_engine.log_suggestions_shown(
                                        actual_user_id,
                                        tool_name,
                                        suggestions_data["suggestions"]
                                    )
                        
                        except Exception as e:
                            logger.warning(f"Failed to generate suggestions for intent: {e}")
                    
                    # ✅ FIX: Return streaming response if requested
                    if stream:
                        async def stream_intent_response():
                            import json as json_module
                            try:
                                session_id = msg.session_id or f"session_{int(time.time() * 1000)}"
                                
                                # Event: session_start
                                yield f"data: {json_module.dumps({'type': 'session_start', 'session_id': session_id, 'timestamp': datetime.now().isoformat()})}\n\n"
                                
                                # Event: agent_state_delta
                                yield f"data: {json_module.dumps({'type': 'agent_state_delta', 'state': {'routing': 'intent_system', 'intent': intent.name}, 'timestamp': datetime.now().isoformat()})}\n\n"
                                
                                # Event: message_delta (stream response word by word)
                                words = response_text.split(' ')
                                for i, word in enumerate(words):
                                    yield f"data: {json_module.dumps({'type': 'message_delta', 'delta': word + (' ' if i < len(words) - 1 else ''), 'timestamp': datetime.now().isoformat()})}\n\n"
                                    await asyncio.sleep(0.02)  # Fast streaming for intent responses
                                
                                # Event: session_end
                                yield f"data: {json_module.dumps({'type': 'session_end', 'session_id': session_id, 'final_state': {'complete': True}, 'timestamp': datetime.now().isoformat()})}\n\n"
                            except Exception as e:
                                logger.error(f"Intent streaming error: {e}")
                                yield f"data: {json_module.dumps({'type': 'error', 'error': {'message': str(e)}, 'timestamp': datetime.now().isoformat()})}\n\n"
                        
                        return StreamingResponse(
                            stream_intent_response(),
                            media_type="text/event-stream",
                            headers={
                                "Cache-Control": "no-cache",
                                "X-Accel-Buffering": "no",
                                "Connection": "keep-alive"
                            }
                        )
                    else:
                        # Return non-streaming JSON response
                        return {
                            "response": response_text,
                            "routing": "intent_system",
                            "intent": intent.name,
                            "tier": intent.tier,
                            "confidence": intent.confidence,
                            "latency_ms": (time.time() - start_intent_time) * 1000
                        }
                else:
                    intent_latency_ms = (time.time() - start_intent_time) * 1000
                    logger.debug(f"No intent match (non-stream), falling back to LLM (classification took {intent_latency_ms:.2f}ms)")
            except Exception as e:
                logger.warning(f"Intent classification failed (non-stream), falling back to LLM: {e}")
        
        # Step 0: Check if this is a self-awareness query (who are you, what can you do)
        if _is_self_awareness_query(msg.message):
            logger.info(f"🎯 Self-awareness query detected: {msg.message}")
            self_awareness_response = await _handle_self_awareness_query(msg.message, actual_user_id)
            
            if stream:
                async def self_awareness_stream():
                    import json as json_module
                    session_id = msg.session_id or f"session_{int(time.time() * 1000)}"
                    yield f"data: {json_module.dumps({'type': 'session_start', 'session_id': session_id, 'timestamp': datetime.now().isoformat()})}\n\n"
                    
                    # Stream the response word by word
                    words = self_awareness_response.split(' ')
                    for i, word in enumerate(words):
                        yield f"data: {json_module.dumps({'type': 'message_delta', 'delta': word + (' ' if i < len(words) - 1 else ''), 'timestamp': datetime.now().isoformat()})}\n\n"
                        await asyncio.sleep(0.02)
                    
                    yield f"data: {json_module.dumps({'type': 'session_end', 'session_id': session_id, 'timestamp': datetime.now().isoformat()})}\n\n"
                
                return StreamingResponse(self_awareness_stream(), media_type="text/event-stream")
            else:
                return {
                    "response": self_awareness_response,
                    "routing": "self_awareness",
                    "interaction_id": f"self_aware_{int(time.time() * 1000)}",
                    "response_time": time.time() - start_time
                }
        
        # Step 1: Detect if this needs orchestration (planning requests)
        needs_orchestration = _is_planning_request(msg.message)
        
        if needs_orchestration and stream:
            logger.info(f"🎯 Detected planning request, using orchestrator: {msg.message}")
            from cross_agent_collaboration import orchestrator
            
            async def orchestration_stream():
                async for event in orchestrator.stream_orchestration(actual_user_id, msg.message, context):
                    yield f"data: {json_module.dumps(event, default=str)}\n\n"
            
            return StreamingResponse(orchestration_stream(), media_type="text/event-stream")
        
        # Step 1: Check if this is a SIMPLE action (single tool call) - skip Enhanced MEM Agent for speed
        is_simple_action = _is_simple_action(msg.message)
        
        # Step 1a: Try Enhanced MEM Agent ONLY for complex multi-step tasks (not simple actions or greetings)
        simple_greetings = ["hi", "hello", "hey", "good morning", "good afternoon", "good evening"]
        is_simple_greeting = msg.message.lower().strip() in simple_greetings
        
        if enhanced_mem_agent and not is_simple_action and not is_simple_greeting:
            try:
                logger.info(f"🤖 Trying Enhanced MEM Agent for complex task: {msg.message}")
                # ✅ FIX: Add timeout to prevent blocking on slow/unavailable service
                try:
                    memories = await asyncio.wait_for(
                        enhanced_mem_agent.enhanced_search(
                            msg.message, 
                            user_id=actual_user_id,
                            execute_actions=True
                        ),
                        timeout=1.0  # ✅ PHASE 3.1: Reduced to 1s for faster fallback
                    )
                except asyncio.TimeoutError:
                    logger.warning("⚠️ Enhanced MEM Agent timeout, skipping")
                    memories = {"fallback": True, "experts": []}
                except Exception as e:
                    logger.warning(f"⚠️ Enhanced MEM Agent error: {e}")
                    memories = {"fallback": True, "experts": []}
                
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
                            "response": clean_llm_response(response),
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
                            "response": clean_llm_response(response),
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
                    # ✅ FIX: If Enhanced MEM Agent didn't execute actions but this is clearly an action request,
                    # ensure we route to action mode so LLM will use tools
                    message_lower = msg.message.lower()
                    action_keywords = ['add', 'create', 'schedule', 'remind', 'set', 'put', 'remove', 'delete']
                    if any(keyword in message_lower for keyword in action_keywords):
                        logger.info(f"🔧 Action keywords detected, ensuring action routing: {msg.message}")
                        routing = await intelligent_routing(msg.message, context)
                        if routing.get("type") != "action":
                            routing["type"] = "action"
                            routing["mcp_tools_needed"] = True
                            logger.info("✅ Forced action routing for tool execution")
            except Exception as e:
                logger.warning(f"Enhanced MEM Agent failed, falling back to conversation: {e}")
                # ✅ FIX: On Enhanced MEM Agent failure, still check if this is an action request
                message_lower = msg.message.lower()
                action_keywords = ['add', 'create', 'schedule', 'remind', 'set', 'put', 'remove', 'delete']
                if any(keyword in message_lower for keyword in action_keywords):
                    logger.info(f"🔧 Action keywords detected after MEM Agent failure, ensuring action routing")
                    routing = await intelligent_routing(msg.message, context)
                    if routing.get("type") != "action":
                        routing["type"] = "action"
                        routing["mcp_tools_needed"] = True
                        logger.info("✅ Forced action routing after MEM Agent failure")
        
        # Step 2: Normal conversation flow with MCP tools context
        # Intelligent routing decision (only if not already set by Enhanced MEM Agent fallback)
        if routing is None:
            routing = await intelligent_routing(msg.message, context)
        
        # ✅ PHASE 1.1: PARALLEL CONTEXT FETCHING - Fetch memories, user_context, and MCP tools in parallel
        # ✅ FAST PATH: Skip for simple greetings to save 3-5 seconds
        if is_simple_greeting:
            # Skip all overhead for simple greetings - go straight to LLM
            memories = {}
            user_context = {"calendar_events": [], "active_lists": [], "recent_journal": [], "people": [], "projects": []}
            tools_context = ""  # Will be fetched later if needed
            logger.info(f"⚡ Fast path: Skipping memory/context for simple greeting")
        else:
            try:
                async with asyncio.timeout(1.0):  # ✅ OPTIMIZED: Reduced to 1s for faster fallback
                    if routing.get("requires_memory"):
                        # ✅ PARALLEL: Fetch all three in parallel
                        memories, user_context, tools_context = await asyncio.gather(
                            search_memories(msg.message, actual_user_id),
                            get_user_context(actual_user_id, query=msg.message),
                            get_mcp_tools_context(),
                            return_exceptions=True  # Don't fail if one fails
                        )
                        # Handle exceptions
                        if isinstance(memories, Exception):
                            logger.warning(f"Memory search failed: {memories}")
                            memories = {}
                        if isinstance(user_context, Exception):
                            logger.warning(f"User context failed: {user_context}")
                            user_context = {"calendar_events": [], "active_lists": [], "recent_journal": [], "people": [], "projects": []}
                        if isinstance(tools_context, Exception):
                            logger.warning(f"MCP tools context failed: {tools_context}")
                            tools_context = ""
                        logger.info(f"✅ Parallel context fetch complete: {len(memories.get('people', []))} people, {len(user_context.get('calendar_events', []))} events")
                    else:
                        # Get episode context even without full memory search (conversational continuity)
                        memories = {}
                        try:
                            temporal_enhancement = await enhance_memory_search_with_temporal("", actual_user_id, "all")
                            memories["episode_context"] = temporal_enhancement.get("episode_context", {})
                            logger.info(f"✅ Got episode context for conversation (no full memory search)")
                        except Exception as e:
                            logger.warning(f"Failed to get episode context: {e}")
                        # ✅ PARALLEL: Fetch user_context and tools_context in parallel
                        user_context, tools_context = await asyncio.gather(
                            get_user_context(actual_user_id, query=msg.message),
                            get_mcp_tools_context(),
                            return_exceptions=True
                        )
                        if isinstance(user_context, Exception):
                            user_context = {"calendar_events": [], "active_lists": [], "recent_journal": [], "people": [], "projects": []}
                        if isinstance(tools_context, Exception):
                            tools_context = ""
            except asyncio.TimeoutError:
                logger.warning(f"Context fetch timed out after 1s, using empty context")
                memories = {}
                # Still try to get episode context on timeout (lightweight operation)
                try:
                    temporal_enhancement = await enhance_memory_search_with_temporal("", actual_user_id, "all")
                    memories["episode_context"] = temporal_enhancement.get("episode_context", {})
                except:
                    pass
                user_context = {"calendar_events": [], "active_lists": [], "recent_journal": [], "people": [], "projects": []}
                tools_context = ""

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
                await generate_streaming_response(msg.message, context, memories, user_context, routing, tools_context),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",  # Disable nginx buffering for real-time streaming
                    "Connection": "keep-alive"
                }
            )
        else:
            # Return regular response
            injected_execution_result = None  # Initialize for non-streaming path
            response = await generate_response(msg.message, context, memories, user_context, routing)
            
            # ✅ FIX: Log the raw response to see if LLM is generating tool calls
            logger.info(f"📝 LLM raw response (first 500 chars): {response[:500]}")
            
            # 🔧 AUTO-INJECT TOOL CALLS if LLM didn't generate them
            if routing.get("type") == "action" and "[TOOL_CALL:" not in response:
                logger.warning(f"⚠️ Action detected but NO tool call generated. Auto-injecting...")
                
                message_lower = msg.message.lower()
                
                # Shopping list patterns
                if any(word in message_lower for word in ["add", "put"]) and any(word in message_lower for word in ["shopping", "list", "grocery"]):
                    # Extract item name
                    item = msg.message
                    for remove in ["add", "Add", "to", "To", "shopping", "Shopping", "list", "List", "my", "My", "the", "The", "a", "A", "please", "Please", "can", "you"]:
                        item = item.replace(remove, "")
                    item = item.strip()
                    
                    if item:
                        tool_call = f'[TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"{item}","priority":"medium"}}]'
                        response = tool_call + "\n" + response
                        logger.info(f"🔧 AUTO-INJECTED shopping list tool call for: {item}")
                
                # Calendar patterns
                elif any(word in message_lower for word in ["schedule", "create event", "add event"]):
                    logger.warning(f"⚠️ Calendar action detected - would auto-inject but needs date parsing")
            
            if "TOOL_CALL" in response:
                logger.info(f"✅ Tool call(s) present in response (generated or injected)")
            
            # Parse and execute any tool calls in the response
            original_response = response
            response = await parse_and_execute_code_or_tools(response, actual_user_id)
            
            # ✅ Prepend injected execution result if available
            if injected_execution_result:
                # Combine injected result with LLM response
                if response and response != original_response:
                    # LLM generated tool calls too - combine results
                    response = f"{injected_execution_result}\n{response}"
                else:
                    # Only injected tool call was executed
                    response = injected_execution_result
                logger.info(f"✅ Included auto-injected execution result in final response")
            
            # ✅ FIX: If no tool calls were executed but this was an action request, log warning
            if response == original_response and (routing.get("type") == "action" or routing.get("mcp_tools_needed")) and not injected_execution_result:
                logger.warning(f"⚠️ Action request '{msg.message}' did not result in tool execution. LLM may need better prompting.")
            
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
                    "response": clean_llm_response(response),
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
                "response": clean_llm_response(response),
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
def _is_simple_query(message: str) -> bool:
    """Detect simple queries that can be handled by lightweight CPU model (phi3:mini)"""
    message_lower = message.lower().strip()
    
    # ONLY simple greetings go to phi3:mini - everything else uses gemma3n-e2b-gpu-fixed
    simple_greetings = ["hi", "hello", "hey", "thanks", "thank you"]
    if message_lower in simple_greetings:
        return True
    
    return False  # All other queries use gemma3n-e2b-gpu-fixed for performance

async def _try_direct_memagent_action(message: str, user_id: str, is_action: bool) -> Optional[str]:
    """
    🎯 TIER 2: Direct MemAgent execution for simple, deterministic actions
    NO LLM needed! Pattern match → API call → Done ⚡
    
    Returns response string if action was executed, None if LLM needed
    """
    if not is_action:
        return None
    
    import re
    import httpx
    
    message_lower = message.lower()
    
    # ===== SHOPPING LIST - Single Item =====
    shopping_patterns = [
        (r'add\s+([\w\s]+?)\s+to\s+(?:my\s+)?(?:shopping|list)', 1),
        (r'put\s+([\w\s]+?)\s+on\s+(?:my\s+)?(?:shopping|list)', 1),
        (r'(?:i\s+)?need\s+to\s+buy\s+([\w]+)', 1),
    ]
    
    for pattern, group in shopping_patterns:
        match = re.search(pattern, message_lower)
        if match:
            item = match.group(group).strip()
            
            # Skip multi-item (use LLM for those)
            if ' and ' in item or ',' in item:
                logger.info(f"⚠️ Multi-item detected: '{item}' - using LLM")
                return None
            
            # Skip junk
            if len(item) < 2 or ' and ' in item:
                continue
            
            # ⚡ DIRECT DATABASE INSERT (10ms, no LLM!)
            try:
                conn = sqlite3.connect("/app/data/zoe.db")
                cursor = conn.cursor()
                
                # Get or create shopping list
                cursor.execute("""
                    SELECT id FROM lists 
                    WHERE user_id = ? AND list_type = 'shopping' 
                    LIMIT 1
                """, (user_id,))
                
                row = cursor.fetchone()
                if row:
                    list_id = row[0]
                else:
                    # Create shopping list
                    cursor.execute("""
                        INSERT INTO lists (user_id, list_type, list_category, name)
                        VALUES (?, 'shopping', 'shopping', 'Shopping List')
                    """, (user_id,))
                    list_id = cursor.lastrowid
                
                # Add item
                cursor.execute("""
                    INSERT INTO list_items (list_id, task_text, priority, completed)
                    VALUES (?, ?, 'medium', 0)
                """, (list_id, item))
                
                conn.commit()
                conn.close()
                
                logger.info(f"⚡ DIRECT: Added '{item}' to shopping list (no LLM!)")
                
                # Notify via WebSocket
                try:
                    from routers.lists import lists_ws_manager
                    import asyncio
                    asyncio.create_task(lists_ws_manager.broadcast_to_user(user_id, {
                        "type": "item_added",
                        "list_type": "shopping",
                        "list_id": list_id,
                        "item": item
                    }))
                except:
                    pass  # WebSocket notification is optional
                
                return f"✅ Added {item} to your shopping list!"
                    
            except Exception as e:
                logger.warning(f"❌ Direct database insert failed: {e}")
                return None
    
    # ===== CALENDAR EVENT - Simple =====
    calendar_patterns = [
        (r'schedule\s+([\w\s]+?)\s+(?:at|for)\s+([\w\s:]+)', 'schedule'),
    ]
    
    for pattern, event_type in calendar_patterns:
        match = re.search(pattern, message_lower)
        if match:
            # Skip complex events (use LLM)
            if 'tomorrow' in message_lower or 'next week' in message_lower:
                logger.info(f"⚠️ Complex time detected - using LLM")
                return None
            
            # For now, defer to LLM for calendar (needs date parsing)
            return None
    
    return None  # No simple action matched


def _auto_inject_tool_call(message: str) -> Optional[str]:
    """
    Auto-inject tool calls for detected action patterns (safety net for 100% success)
    Returns the tool call string if one should be injected, None otherwise
    """
    import re
    from datetime import datetime, timedelta
    
    message_lower = message.lower()
    
    # ===== SHOPPING LIST PATTERNS =====
    # Pattern: "add X to shopping/list" - match ONE item at a time
    add_patterns = [
        # Direct: "add X to shopping/list"
        (r'add\s+([a-z]+(?:\s+[a-z]+)?)\s+to\s+(?:my\s+)?(?:shopping|list)', 1),
        (r'put\s+([a-z]+(?:\s+[a-z]+)?)\s+on\s+(?:my\s+)?(?:shopping|list)', 1),
        # Natural: "I need to buy X"
        (r'(?:i\s+)?need\s+to\s+buy\s+([a-z]+)', 1),
        (r'(?:i\s+)?need\s+to\s+get\s+([a-z]+)', 1),
        (r'(?:i\s+)?need\s+to\s+pick\s+up\s+([a-z]+)', 1),
        (r'(?:i\s+)?should\s+get\s+(?:some\s+)?([a-z]+)', 1),
        (r'(?:i\s+)?should\s+buy\s+(?:some\s+)?([a-z]+)', 1),
        # Conversational: "we're out of X"
        (r'(?:we\'re|we\s+are)\s+out\s+of\s+([a-z]+)', 1),
        (r'(?:i\s+)?noticed\s+we\s+need\s+([a-z]+)', 1),
        (r'(?:running|ran)\s+low\s+on\s+([a-z]+)', 1),
        (r'could\s+use\s+(?:some\s+)?([a-z]+)', 1),
        # Action verbs: "grab/get/buy X"
        (r'grab\s+(?:some\s+)?([a-z]+)', 1),
        (r'get\s+(?:some\s+)?([a-z]+)(?:\s+please)?', 1),
        (r'buy\s+(?:some\s+)?([a-z]+)', 1),
        (r'purchase\s+([a-z]+)', 1),
        # Remember: "don't forget to buy X"
        (r'don\'t\s+(?:let\s+me\s+)?forget\s+to\s+buy\s+([a-z]+)', 1),
        (r'remember\s+to\s+buy\s+([a-z]+)', 1),
        # Going to: "I'm going to need X"
        (r'(?:i\'m|i\s+am)\s+going\s+to\s+need\s+([a-z]+)', 1),
        (r'better\s+get\s+(?:some\s+)?([a-z]+)', 1),
    ]
    
    for pattern, group in add_patterns:
        match = re.search(pattern, message_lower)
        if match:
            item = match.group(group).strip()
            # Skip if captured "and" or other junk
            if ' and ' in item or len(item) < 2:
                continue
            return f'[TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"{item}","priority":"medium"}}]'
    
    # ===== CALENDAR EVENT PATTERNS =====
    # Pattern: "schedule X at Y" or "create event Z"
    calendar_patterns = [
        (r'schedule\s+([a-z\s]+?)(?:\s+(?:at|for|on)\s+([a-z0-9\s:]+))?', 'schedule'),
        (r'create\s+(?:a\s+)?(?:calendar\s+)?event\s+([a-z\s]+?)(?:\s+(?:at|for|on)\s+([a-z0-9\s:]+))?', 'create'),
        (r'add\s+(?:a\s+)?(?:calendar\s+)?event\s+([a-z\s]+?)(?:\s+(?:at|for|on)\s+([a-z0-9\s:]+))?', 'create'),
        (r'(?:book|set)\s+(?:an?\s+)?appointment\s+([a-z\s]+?)(?:\s+(?:at|for|on)\s+([a-z0-9\s:]+))?', 'appointment'),
        (r'remind\s+me\s+to\s+([a-z\s]+?)(?:\s+(?:at|for|on)\s+([a-z0-9\s:]+))?', 'reminder'),
    ]
    
    for pattern, event_type in calendar_patterns:
        match = re.search(pattern, message_lower)
        if match:
            title = match.group(1).strip()
            time_info = match.group(2).strip() if len(match.groups()) > 1 and match.group(2) else ""
            
            # Parse time info for date/time
            tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
            start_date = tomorrow  # Default to tomorrow
            start_time = "14:00"  # Default to 2pm
            
            if time_info:
                # Extract time if present (e.g., "2pm", "14:00", "10am")
                time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', time_info)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = time_match.group(2) if time_match.group(2) else "00"
                    am_pm = time_match.group(3)
                    
                    if am_pm == 'pm' and hour < 12:
                        hour += 12
                    elif am_pm == 'am' and hour == 12:
                        hour = 0
                    
                    start_time = f"{hour:02d}:{minute}"
                
                # Check for "today" vs "tomorrow"
                if 'today' in time_info:
                    start_date = datetime.now().strftime('%Y-%m-%d')
                elif 'tomorrow' in time_info:
                    start_date = tomorrow
                elif 'next week' in time_info:
                    start_date = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
            
            if title and len(title) > 1:
                return f'[TOOL_CALL:create_calendar_event:{{"title":"{title}","start_date":"{start_date}","start_time":"{start_time}"}}]'
    
    # ===== PERSONAL FACTS PATTERNS (SELF) =====
    # Pattern: "My X is Y" - store facts about the user
    self_fact_patterns = [
        # Direct: "My favorite X is Y"
        (r'my\s+favorite\s+([a-z_]+)\s+is\s+(.+?)(?:\.|$)', 'favorite_%s'),
        # "I like/love X"
        (r'(?:i\s+)?(?:like|love|enjoy|prefer)\s+([a-z\s]+?)(?:\s+(?:music|movies|books|food))?(?:\.|$)', None),
        # "My X is Y" (general)
        (r'my\s+([a-z_]+)\s+is\s+(.+?)(?:\.|$)', '%s'),
        # "I am a/an X"
        (r'(?:i\s+am|i\'m)\s+(?:a|an)\s+([a-z\s]+?)(?:\.|$)', 'occupation'),
        # "I work as X"
        (r'(?:i\s+)?work\s+as\s+(?:a|an)?\s*([a-z\s]+?)(?:\.|$)', 'job'),
    ]
    
    for pattern, key_template in self_fact_patterns:
        match = re.search(pattern, message_lower)
        if match:
            if key_template is None:
                # "I like X" pattern
                value = match.group(1).strip()
                key = "interests"
            elif '%s' in key_template:
                # Template with placeholder
                key_part = match.group(1).strip().replace(' ', '_')
                key = key_template % key_part
                value = match.group(2).strip() if len(match.groups()) > 1 else key_part
            else:
                # Fixed key
                key = key_template
                value = match.group(1).strip()
            
            # Clean up value
            value = value.rstrip('.!?')
            if len(value) > 1 and len(key) > 1:
                return f'[TOOL_CALL:store_self_fact:{{"fact_key":"{key}","fact_value":"{value}"}}]'
    
    # ===== PERSONAL FACTS RETRIEVAL =====
    # Pattern: "What is my X?" - retrieve facts about the user
    self_info_patterns = [
        (r'what\s+is\s+my\s+([a-z_\s]+?)\?', None),
        (r'what\'s\s+my\s+([a-z_\s]+?)\?', None),
        (r'what\s+do\s+i\s+(like|love|enjoy|prefer)\?', 'interests'),
        (r'tell\s+me\s+about\s+(?:my|myself)', None),
    ]
    
    for pattern, fixed_key in self_info_patterns:
        match = re.search(pattern, message_lower)
        if match:
            if fixed_key:
                key = fixed_key
            elif fixed_key is None and len(match.groups()) > 0:
                key = match.group(1).strip().replace(' ', '_')
            else:
                key = ""  # Get all info
            
            if key:
                return f'[TOOL_CALL:get_self_info:{{"fact_key":"{key}"}}]'
            else:
                return f'[TOOL_CALL:get_self_info:{{}}]'
    
    return None

def _is_simple_action(message: str) -> bool:
    """Detect if this is a simple single-action request (skip Enhanced MEM Agent for speed)"""
    message_lower = message.lower()
    
    # Simple action patterns (single tool call)
    simple_patterns = [
        r'\badd\s+(?:to\s+)?(?:shopping\s+)?list\b',
        r'\badd\s+\w+\s+to\s+\w+\s+list\b',
        r'\bcreate\s+(?:calendar\s+)?event\b',
        r'\bschedule\s+\w+\b',
        r'\bshow\s+my\s+(?:shopping\s+)?list\b',
        r'\bget\s+my\s+(?:shopping\s+)?list\b',
    ]
    
    # Check if it matches a simple pattern
    import re
    for pattern in simple_patterns:
        if re.search(pattern, message_lower):
            return True
    
    # If it has "and" or multiple verbs, it's probably complex
    action_words = ['add', 'create', 'schedule', 'remind', 'set', 'put', 'remove', 'delete']
    action_count = sum(1 for word in action_words if word in message_lower)
    if action_count > 1:
        return False
    
    # If it has "and" or "then", it's complex
    if ' and ' in message_lower or ' then ' in message_lower:
        return False
    
    # Single action word = simple
    if action_count == 1:
        return True
    
    return False

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

def _is_self_awareness_query(message: str) -> bool:
    """Detect if user is asking about Zoe's identity, capabilities, or self-concept"""
    self_awareness_patterns = [
        # Identity questions
        "who are you", "what are you", "tell me about yourself",
        "introduce yourself", "who is zoe", "what is zoe",
        # Capability questions
        "what can you do", "what can you help", "what do you do",
        "what are you capable", "what are your capabilities",
        "what features", "what can i ask",
        # Self-awareness
        "describe yourself", "what are you good at", "what are your skills",
        "what can i use you for", "how can you help", "what help can you provide"
    ]
    message_lower = message.lower()
    return any(phrase in message_lower for phrase in self_awareness_patterns)

async def _handle_self_awareness_query(message: str, user_id: str) -> str:
    """Handle self-awareness queries with identity-aware responses"""
    try:
        from self_awareness import self_awareness
        
        # Set user context
        self_awareness.set_user_context(user_id)
        
        # Determine if brief or detailed response is needed
        brief_patterns = ["quickly", "brief", "short", "summary"]
        is_brief = any(pattern in message.lower() for pattern in brief_patterns)
        
        # Get appropriate self-description
        description = await self_awareness.get_self_description(brief=is_brief)
        
        return description
    except Exception as e:
        logger.error(f"Self-awareness query failed: {e}")
        # Fallback response
        return """I'm Zoe, your personal AI assistant. I can help you manage shopping lists, calendar events, tasks, notes, journal entries, and more. I remember our conversations and learn from our interactions. I can also control your smart home devices and automate workflows. What can I help you with today?"""

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
