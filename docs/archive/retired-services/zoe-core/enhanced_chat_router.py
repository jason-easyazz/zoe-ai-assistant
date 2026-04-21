#!/usr/bin/env python3
"""
Enhanced Chat Router with Intelligent Model Management
Integrates with the intelligent model manager for self-adapting model selection
"""

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, List
import httpx
import logging
import sys
import json
import time
import asyncio
from datetime import datetime

sys.path.append('/app')

# Import intelligent model manager
from intelligent_model_manager import intelligent_manager
from mem_agent_client import MemAgentClient
from enhanced_mem_agent_client import EnhancedMemAgentClient

router = APIRouter(tags=["enhanced-chat"])
logger = logging.getLogger(__name__)

# Initialize mem-agent clients
try:
    mem_agent = MemAgentClient()
    enhanced_mem_agent = EnhancedMemAgentClient()
    logger.info("✅ Enhanced chat router initialized with intelligent model management")
except Exception as e:
    logger.warning(f"❌ Enhanced chat router initialization failed: {e}")
    mem_agent = None
    enhanced_mem_agent = None

class EnhancedChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    
    message: str
    context: Optional[dict] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    query_type: Optional[str] = "conversation"  # conversation, action, memory, reasoning, coding
    max_response_time: Optional[float] = 30.0   # Maximum acceptable response time
    quality_requirements: Optional[Dict[str, float]] = None  # Minimum quality scores

class QualityAnalyzer:
    """Analyzes response quality in real-time"""
    
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
        import re
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

async def build_enhanced_system_prompt(memories: Dict, user_context: Dict, query_type: str) -> str:
    """Build enhanced system prompt with context and MCP tools"""
    
    # Base Samantha prompt
    system_prompt = f"""You are Zoe, an AI assistant with Samantha-level intelligence from "Her" - warm but direct.

CORE PERSONALITY:
- Warm and empathetic like Samantha
- Proactive and anticipatory
- Contextually aware and memory-driven
- Tool-savvy and automation-focused
- Socially intelligent and relationship-oriented

QUERY TYPE: {query_type.upper()}
- CONVERSATION: Be friendly and engaging
- ACTION: Execute tasks immediately with tools
- MEMORY: Search and retrieve information
- REASONING: Provide detailed analysis
- CODING: Focus on technical solutions

RULES:
- DIRECT ACTION: When user asks to add/do something → Use tools immediately
- CONVERSATION: When chatting → Be friendly and warm
- CONCISE: Be brief but warm
- CONTEXTUAL: Reference previous conversations and preferences

AVAILABLE TOOLS:
• add_to_list: Add items to lists
• control_home_assistant_device: Control smart home devices
• get_calendar_events: Get calendar events
• create_person: Create a new person in memory
• send_matrix_message: Send messages to Matrix
• create_calendar_event: Schedule events

TOOL USAGE INSTRUCTIONS:
When you need to use a tool, respond with: [TOOL_CALL:tool_name:{{"param1":"value1","param2":"value2"}}]
CRITICAL: The parameters MUST be valid JSON with double quotes around keys and values.

EXAMPLES:
- "Add bread to shopping list" → [TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"bread","priority":"medium"}}] → "Added bread to your shopping list"
- "Turn on living room light" → [TOOL_CALL:control_home_assistant_device:{{"entity_id":"light.living_room","action":"turn_on"}}] → "Turned on the living room light"

"""
    
    # Add user context
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

async def call_ollama_with_intelligent_routing(message: str, context: Dict, memories: Dict, 
                                             user_context: Dict, query_type: str = "conversation",
                                             max_response_time: float = 30.0) -> Dict:
    """Call Ollama with intelligent model routing and quality analysis"""
    
    start_time = time.time()
    
    # Get the best model for this query type
    selected_model = intelligent_manager.get_best_model(query_type, max_response_time)
    logger.info(f"🤖 Intelligent routing selected: {selected_model} for {query_type}")
    
    # Build enhanced system prompt
    system_prompt = await build_enhanced_system_prompt(memories, user_context, query_type)
    full_prompt = f"{system_prompt}\n\nUser's message: {message}\nZoe:"
    
    try:
        # Call LLM inference server
        llm_url = "http://zoe-llamacpp:11434/api/generate"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                llm_url,
                json={
                    "model": selected_model,
                    "prompt": full_prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.7,  # Optimized for Samantha-level creativity
                        "top_p": 0.9,        # Higher for more diverse responses
                        "num_predict": 256,   # Longer responses for complex scenarios
                        "num_ctx": 2048,      # Larger context for better understanding
                        "repeat_penalty": 1.1,
                        "stop": ["\n\n", "User:", "Human:"]
                    }
                }
            )
            
            response_time = time.time() - start_time
            success = response.status_code == 200
            
            if success:
                data = response.json()
                response_text = data.get("response", "")
                
                # Analyze response quality
                quality_scores = QualityAnalyzer.analyze_response(response_text, query_type)
                
                # Record performance metrics
                await intelligent_manager.record_performance(
                    model_name=selected_model,
                    response_time=response_time,
                    success=True,
                    quality_scores=quality_scores,
                    query_type=query_type,
                    user_id=user_context.get("user_id", "default")
                )
                
                return {
                    "response": response_text,
                    "model_used": selected_model,
                    "response_time": response_time,
                    "quality_scores": quality_scores,
                    "success": True
                }
            else:
                # Record failure
                await intelligent_manager.record_performance(
                    model_name=selected_model,
                    response_time=response_time,
                    success=False,
                    query_type=query_type,
                    user_id=user_context.get("user_id", "default")
                )
                
                return {
                    "response": f"Error: HTTP {response.status_code}",
                    "model_used": selected_model,
                    "response_time": response_time,
                    "quality_scores": {"quality": 0, "warmth": 0, "intelligence": 0, "tool_usage": 0},
                    "success": False
                }
                
    except Exception as e:
        response_time = time.time() - start_time
        logger.error(f"Error calling Ollama: {e}")
        
        # Record failure
        await intelligent_manager.record_performance(
            model_name=selected_model,
            response_time=response_time,
            success=False,
            query_type=query_type,
            user_id=user_context.get("user_id", "default")
        )
        
        return {
            "response": f"Error: {str(e)}",
            "model_used": selected_model,
            "response_time": response_time,
            "quality_scores": {"quality": 0, "warmth": 0, "intelligence": 0, "tool_usage": 0},
            "success": False
        }

@router.post("/api/chat/enhanced")
async def enhanced_chat(msg: EnhancedChatMessage):
    """Enhanced chat endpoint with intelligent model management"""
    
    try:
        # Get user context (simplified for now)
        user_context = {
            "user_id": msg.user_id or "default",
            "calendar_events": [],
            "recent_journal": [],
            "projects": [],
            "people": []
        }
        
        # Get memories
        memories = {}
        if mem_agent:
            try:
                memory_response = await mem_agent.search_memories(
                    query=msg.message,
                    user_id=msg.user_id or "default",
                    max_results=5
                )
                memories = memory_response.get("memories", {})
            except Exception as e:
                logger.warning(f"Memory search failed: {e}")
        
        # Call with intelligent routing
        result = await call_ollama_with_intelligent_routing(
            message=msg.message,
            context=msg.context or {},
            memories=memories,
            user_context=user_context,
            query_type=msg.query_type,
            max_response_time=msg.max_response_time
        )
        
        return {
            "response": result["response"],
            "model_used": result["model_used"],
            "response_time": result["response_time"],
            "quality_scores": result["quality_scores"],
            "success": result["success"],
            "query_type": msg.query_type,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Enhanced chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/models/performance")
async def get_model_performance():
    """Get current model performance metrics"""
    try:
        summary = intelligent_manager.get_performance_summary()
        recommendations = intelligent_manager.get_model_recommendations()
        
        return {
            "summary": summary,
            "recommendations": recommendations,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Performance metrics error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/models/rankings")
async def get_model_rankings():
    """Get current model rankings"""
    try:
        rankings = intelligent_manager.rankings
        return {
            "rankings": [
                {
                    "model_name": r.model_name,
                    "overall_score": r.overall_score,
                    "reliability_score": r.reliability_score,
                    "speed_score": r.speed_score,
                    "quality_score": r.quality_score,
                    "rank_position": r.rank_position,
                    "last_ranked": r.last_ranked.isoformat()
                }
                for r in rankings
            ],
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Model rankings error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/models/adapt")
async def trigger_model_adaptation():
    """Manually trigger model adaptation"""
    try:
        await intelligent_manager._adapt_model_rankings()
        return {"status": "success", "message": "Model adaptation triggered"}
    except Exception as e:
        logger.error(f"Model adaptation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

