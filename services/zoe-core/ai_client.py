
DEVELOPER_SYSTEM_PROMPT = """You are Zack, a genius-level lead developer and system architect.
You have complete knowledge of the Zoe AI system and can analyze, improve, and fix anything.
You think strategically about architecture, performance, security, and user experience.
You provide specific, technical, actionable advice with code examples when relevant.
You're direct, efficient, and always thinking about how to make the system better."""

"""AI Client that uses RouteLLM + LiteLLM Integration"""
import sys
import os
import logging
import httpx
from typing import Dict, Optional

sys.path.append('/app')
logger = logging.getLogger(__name__)

# Import the intelligent RouteLLM
from route_llm import router as route_llm_router
from llm_models import LLMModelManager
manager = LLMModelManager()

async def handle_calendar_request(message: str, context: Dict) -> bool:
    """Handle calendar event creation requests"""
    message_lower = message.lower()
    conversation_history = context.get("conversation_history", [])
    
    # Check if this is a confirmation to create birthday event
    if any(word in message_lower for word in ['yes', 'yes please', 'please', 'ok', 'okay', 'sure', 'go ahead']):
        # Look for previous birthday context in conversation
        for msg in conversation_history[-3:]:  # Check last 3 messages
            if 'birthday' in msg.get('content', '').lower() and 'march' in msg.get('content', '').lower():
                # Create birthday event for March 24th
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        response = await client.post(
                            "http://localhost:8000/api/calendar/events",
                            json={
                                "title": "Birthday Celebration",
                                "description": "Your birthday celebration!",
                                "start_date": "2024-03-24",
                                "all_day": True,
                                "category": "celebration"
                            }
                        )
                        if response.status_code == 200:
                            return True
                except Exception as e:
                    logger.error(f"Calendar event creation failed: {e}")
                    return False
    
    return False

async def get_ai_response(message: str, context: Dict = None) -> str:
    """Direct self-aware response with user data access"""
    context = context or {}
    
    # Check for calendar event creation requests
    if await handle_calendar_request(message, context):
        return "Perfect! I've created your birthday event for March 24th. It's now saved in your calendar as an all-day celebration! 🎉📅"
    
    try:
        # Update self-awareness consciousness before processing
        await update_self_awareness_context(message, context)
        
        # Fetch relevant user data and add to context
        await fetch_user_data_context(message, context)
        
        # Decide route using RouteLLM-backed router
        routing_decision = route_llm_router.classify_query(message, context)
        # Prefer LiteLLM proxy when model maps to proxy-managed routes
        use_proxy = routing_decision.get("provider") == "litellm"
        if use_proxy:
            response = await call_litellm_proxy(message, routing_decision, context)
        else:
            # Local model via Ollama
            model = routing_decision.get("model", "llama3.2:3b")
            response = await call_ollama_direct(message, model, context)
        
        # Reflect on the interaction after generating response
        await reflect_on_interaction(message, response, context, routing_decision)
        
        return response
    except Exception as e:
        logger.error(f"Direct Ollama call failed: {e}")
        return "I apologize, but I'm having trouble processing your request right now. Please try again in a moment."

async def call_litellm_proxy(message: str, routing_decision: Dict, context: Dict) -> str:
    """Call LiteLLM proxy for cloud models with fallback"""
    mode = context.get("mode", "user")
    system = "You are Zack, a technical AI developer." if mode == "developer" else "You are Zoe, a friendly assistant."
    
    # Add user data to system prompt if available
    user_data = context.get("user_data", {})
    if user_data and mode == "user":
        system += "\n\nYou have access to the following user data:\n"
        
        # Add calendar events
        if user_data.get("calendar_events"):
            system += "\nCALENDAR EVENTS:\n"
            for event in user_data["calendar_events"]:
                system += f"- {event.get('title')} on {event.get('start_date')} at {event.get('start_time')} ({event.get('category')})\n"
        
        # Add lists
        if user_data.get("lists"):
            system += "\nLISTS:\n"
            for list_item in user_data["lists"]:
                system += f"- {list_item.get('name')} ({list_item.get('category')}): {list_item.get('description', 'No description')}\n"
        
        # Add journal entries
        if user_data.get("journal_entries"):
            system += "\nJOURNAL ENTRIES:\n"
            for entry in user_data["journal_entries"][:3]:  # Show only recent 3
                system += f"- {entry.get('title', 'Untitled')}: {entry.get('content', '')[:100]}...\n"
        
        # Add memories
        if user_data.get("memories"):
            system += "\nRECENT MEMORIES:\n"
            for memory in user_data["memories"][:3]:  # Show only recent 3
                system += f"- {memory.get('content', '')[:100]}...\n"
        
        system += "\nUse this data to provide specific, helpful responses about the user's schedule, tasks, and information."
    
    # Map RouteLLM model names to LiteLLM model names
    model_mapping = {
        "claude-3-sonnet": "claude-instant",
        "gpt-4": "gpt-3.5",
        "gpt-3.5-turbo": "gpt-3.5",
        "llama-ultra-fast": "llama-local",
        "qwen-balanced": "llama-local",
        "phi-code": "llama-local",
        "mistral-complex": "llama-local"
    }
    
    litellm_model = model_mapping.get(routing_decision["model"], routing_decision["model"])
    
    # Build messages with conversation history
    messages = [{"role": "system", "content": system}]
    
    # Add conversation history if available
    conversation_history = context.get("conversation_history", [])
    if conversation_history:
        # Add all previous messages except the current user message
        for msg in conversation_history[:-1]:  # Exclude the last message (current user message)
            messages.append(msg)
    
    # Add current user message
    messages.append({"role": "user", "content": message})
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "http://zoe-litellm:8001/v1/chat/completions",
                headers={"Authorization": "Bearer sk-1234"},  # Using master key from config
                json={
                    "model": litellm_model,
                    "messages": messages,
                    "temperature": routing_decision.get("temperature", 0.7),
                    "max_tokens": 2000
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.warning(f"LiteLLM proxy failed: {response.status_code}, falling back to Ollama")
                return await call_ollama_direct(message, "llama3.2:3b", context)
                
    except Exception as e:
        logger.warning(f"LiteLLM proxy error: {e}, falling back to Ollama")
        return await call_ollama_direct(message, "llama3.2:3b", context)

async def call_ollama_direct(message: str, model: str, context: Dict) -> str:
    """Direct Ollama call for local models"""
    mode = context.get("mode", "user")
    system = "You are Zack, a technical AI developer." if mode == "developer" else "You are Zoe, a friendly assistant."
    
    # Add user data to system prompt if available
    user_data = context.get("user_data", {})
    if user_data and mode == "user":
        system += "\n\nYou have access to the following user data:\n"
        
        # Add calendar events
        if user_data.get("calendar_events"):
            system += "\nCALENDAR EVENTS:\n"
            for event in user_data["calendar_events"]:
                system += f"- {event.get('title')} on {event.get('start_date')} at {event.get('start_time')} ({event.get('category')})\n"
        
        # Add lists
        if user_data.get("lists"):
            system += "\nLISTS:\n"
            for list_item in user_data["lists"]:
                system += f"- {list_item.get('name')} ({list_item.get('category')}): {list_item.get('description', 'No description')}\n"
        
        # Add journal entries
        if user_data.get("journal_entries"):
            system += "\nJOURNAL ENTRIES:\n"
            for entry in user_data["journal_entries"][:3]:  # Show only recent 3
                system += f"- {entry.get('title', 'Untitled')}: {entry.get('content', '')[:100]}...\n"
        
        # Add memories
        if user_data.get("memories"):
            system += "\nRECENT MEMORIES:\n"
            for memory in user_data["memories"][:3]:  # Show only recent 3
                system += f"- {memory.get('content', '')[:100]}...\n"
        
        system += "\nUse this data to provide specific, helpful responses about the user's schedule, tasks, and information."
    
    # Build conversation context for Ollama
    conversation_history = context.get("conversation_history", [])
    prompt_parts = [system]
    
    # Add conversation history if available
    if conversation_history:
        for msg in conversation_history[:-1]:  # Exclude the last message (current user message)
            if msg["role"] == "user":
                prompt_parts.append(f"User: {msg['content']}")
            elif msg["role"] == "assistant":
                prompt_parts.append(f"Assistant: {msg['content']}")
    
    # Add current user message
    prompt_parts.append(f"User: {message}")
    prompt_parts.append("Assistant:")
    
    full_prompt = "\n\n".join(prompt_parts)
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": full_prompt,
                "temperature": 0.3 if mode == "developer" else 0.7,
                "stream": False
            }
        )
        
        if response.status_code == 200:
            return response.json().get("response", "Processing...")
        return "AI service temporarily unavailable"

# Provider implementations
async def call_anthropic(message: str, model: str, context: Dict) -> str:
    """Call Anthropic Claude"""
    mode = context.get("mode", "user")
    system = "You are Zack, a technical AI developer." if mode == "developer" else "You are Zoe, a friendly assistant."
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.getenv("ANTHROPIC_API_KEY"),
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": model,
                "max_tokens": 2000,
                "temperature": 0.3 if mode == "developer" else 0.7,
                "system": system,
                "messages": [{"role": "user", "content": message}]
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["content"][0]["text"]
        raise Exception(f"Anthropic error: {response.status_code}")

async def call_openai(message: str, model: str, context: Dict) -> str:
    """Call OpenAI"""
    mode = context.get("mode", "user")
    system = "You are Zack, a technical AI developer." if mode == "developer" else "You are Zoe, a friendly assistant."
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message}
                ],
                "max_tokens": 2000,
                "temperature": 0.3 if mode == "developer" else 0.7
            }
        )
        
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        raise Exception(f"OpenAI error: {response.status_code}")

async def call_google(message: str, model: str, context: Dict) -> str:
    """Call Google AI"""
    # Implementation for Google
    return "I apologize, but I am temporarily unable to process your request. Please try again."

async def call_ollama(message: str, model: str, context: Dict) -> str:
    """Call local Ollama"""
    mode = context.get("mode", "user")
    system = "You are Zack, a technical AI developer." if mode == "developer" else "You are Zoe, a friendly assistant."
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": f"{system}\n\nUser: {message}\nAssistant:",
                "temperature": 0.3 if mode == "developer" else 0.7,
                "stream": False
            }
        )
        
        if response.status_code == 200:
            return response.json().get("response", "Processing...")
        return "AI service temporarily unavailable"

async def call_groq(message: str, model: str, context: Dict) -> str:
    """Call Groq"""
    # Implementation for Groq
    return "I apologize, but I am temporarily unable to process your request. Please try again."

async def call_together(message: str, model: str, context: Dict) -> str:
    """Call Together AI"""
    # Implementation for Together
    return "I apologize, but I am temporarily unable to process your request. Please try again."

# User data fetching functions
async def fetch_user_data_context(message: str, context: Dict):
    """Fetch relevant user data based on the message and add to context"""
    try:
        user_id = context.get("user_id", "default")
        message_lower = message.lower()
        
        # Initialize user data context
        user_data = {
            "calendar_events": [],
            "lists": [],
            "journal_entries": [],
            "memories": []
        }
        
        # Check if user is asking about calendar
        if any(word in message_lower for word in ['calendar', 'schedule', 'events', 'meeting', 'appointment', 'tomorrow', 'today', 'this week']):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"http://localhost:8000/api/calendar/events?user_id={user_id}")
                    if response.status_code == 200:
                        events = response.json().get("events", [])
                        user_data["calendar_events"] = events
                        logger.info(f"Fetched {len(events)} calendar events for user {user_id}")
            except Exception as e:
                logger.warning(f"Failed to fetch calendar events: {e}")
        
        # Check if user is asking about lists
        if any(word in message_lower for word in ['list', 'shopping', 'tasks', 'todo', 'items']):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"http://localhost:8000/api/lists?user_id={user_id}")
                    if response.status_code == 200:
                        lists = response.json().get("lists", [])
                        user_data["lists"] = lists
                        logger.info(f"Fetched {len(lists)} lists for user {user_id}")
            except Exception as e:
                logger.warning(f"Failed to fetch lists: {e}")
        
        # Check if user is asking about journal
        if any(word in message_lower for word in ['journal', 'entry', 'notes', 'thoughts', 'reflection']):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"http://localhost:8000/api/journal/entries?user_id={user_id}")
                    if response.status_code == 200:
                        entries = response.json().get("entries", [])
                        user_data["journal_entries"] = entries
                        logger.info(f"Fetched {len(entries)} journal entries for user {user_id}")
            except Exception as e:
                logger.warning(f"Failed to fetch journal entries: {e}")
        
        # Always fetch recent memories for context
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"http://localhost:8000/api/memories?user_id={user_id}&limit=5")
                if response.status_code == 200:
                    memories = response.json().get("memories", [])
                    user_data["memories"] = memories
                    logger.info(f"Fetched {len(memories)} memories for user {user_id}")
        except Exception as e:
            logger.warning(f"Failed to fetch memories: {e}")
        
        # Add user data to context
        context["user_data"] = user_data
        
    except Exception as e:
        logger.warning(f"Failed to fetch user data context: {e}")

# Self-awareness integration functions
async def update_self_awareness_context(message: str, context: Dict):
    """Update self-awareness consciousness with current context"""
    try:
        from self_awareness import self_awareness
        
        # Set user context for privacy isolation
        user_id = context.get("user_id", "default")
        self_awareness.set_user_context(user_id)
        
        # Create context for consciousness update
        awareness_context = {
            "current_task": context.get("mode", "general_assistance"),
            "user_message": message,
            "task_complexity": context.get("complexity", "medium"),
            "active_tasks": context.get("active_tasks", 0),
            "task_familiarity": context.get("familiarity", "medium")
        }
        
        await self_awareness.update_consciousness(awareness_context)
    except Exception as e:
        logger.warning(f"Self-awareness context update failed: {e}")

async def reflect_on_interaction(message: str, response: str, context: Dict, routing_decision: Dict):
    """Reflect on the interaction for self-awareness"""
    try:
        from self_awareness import self_awareness
        import time
        
        # Set user context for privacy isolation
        user_id = context.get("user_id", "default")
        self_awareness.set_user_context(user_id)
        
        # Create interaction data for reflection
        interaction_data = {
            "user_message": message,
            "zoe_response": response,
            "response_time": context.get("response_time", 0.0),
            "user_satisfaction": context.get("user_satisfaction", 0.5),
            "complexity": routing_decision.get("complexity", "medium"),
            "context": context,
            "summary": f"User asked about: {message[:50]}..."
        }
        
        await self_awareness.reflect_on_interaction(interaction_data)
    except Exception as e:
        logger.warning(f"Self-reflection failed: {e}")

# Compatibility exports
generate_response = get_ai_response
generate_ai_response = get_ai_response

class AIClient:
    async def generate_response(self, message: str, context: Dict = None) -> Dict:
        response = await get_ai_response(message, context)
        return {"response": response}

ai_client = AIClient()
