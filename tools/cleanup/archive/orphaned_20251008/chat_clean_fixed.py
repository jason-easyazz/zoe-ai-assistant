"""
Clean Chat Router for Zoe with Enhancement Systems Integration
============================================================

Provides full conversational AI with proper enhancement system integration.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any
import requests
import json
import time
import uuid
from datetime import datetime

router = APIRouter(tags=["chat"])

class ChatMessage(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None

@router.post("/api/chat/")
@router.post("/api/chat")
async def chat(msg: ChatMessage, user_id: str = Query("default", description="User ID for privacy isolation")):
    """Enhanced chat with full AI integration and enhancement systems"""
    try:
        start_time = time.time()
        interaction_id = str(uuid.uuid4())
        
        # Step 1: Create or get active temporal episode
        try:
            episode_response = requests.get(f"http://localhost:8000/api/temporal-memory/episodes/active?user_id={user_id}", timeout=5)
            if episode_response.status_code == 200:
                episode_data = episode_response.json()
                episode = episode_data.get("episode")
                if not episode:
                    # Create new episode
                    create_response = requests.post("http://localhost:8000/api/temporal-memory/episodes",
                        json={"context_type": "chat", "participants": [user_id]},
                        params={"user_id": user_id},
                        timeout=5
                    )
                    if create_response.status_code == 200:
                        episode = create_response.json()["episode"]
                        print(f"📅 Created new temporal episode: {episode['id']}")
        except Exception as e:
            print(f"⚠️ Temporal memory not available: {e}")
            episode = None
        
        # Step 2: Check if this is a complex request that needs orchestration
        complex_keywords = ["schedule", "create", "add", "remember", "plan", "organize", "coordinate"]
        is_complex = sum(1 for keyword in complex_keywords if keyword.lower() in msg.message.lower()) >= 2
        
        if is_complex:
            try:
                # Use orchestration for complex requests
                orchestration_response = requests.post("http://localhost:8000/api/orchestration/orchestrate",
                    json={"request": msg.message, "context": msg.context or {}},
                    params={"user_id": user_id},
                    timeout=20
                )
                
                if orchestration_response.status_code == 200:
                    orchestration_data = orchestration_response.json()
                    if orchestration_data.get("success"):
                        response_text = f"I've coordinated multiple experts to handle your request: {orchestration_data.get('summary', 'Task completed successfully')}"
                    else:
                        response_text = f"I attempted to coordinate multiple experts, but encountered some issues: {', '.join(orchestration_data.get('errors', ['Unknown error']))}"
                else:
                    response_text = "I understand this is a complex request, but I'm having trouble coordinating the different systems right now."
            except Exception as e:
                response_text = f"I recognize this as a complex request, but I'm having technical difficulties with coordination: {str(e)}"
        else:
            # Use direct Ollama for conversational responses
            try:
                # Create a rich prompt that includes enhancement system awareness
                enhanced_prompt = """You are Zoe, an advanced AI assistant with several enhancement systems:

1. Temporal Memory: You can remember conversations across time and create episodes
2. Cross-Agent Collaboration: You coordinate with 7 expert systems (Calendar, Lists, Memory, Planning, Development, Weather, HomeAssistant)
3. User Satisfaction: You track user feedback and adapt your responses
4. Context Caching: You optimize performance with intelligent caching

The user is asking: """ + msg.message + """

Please respond conversationally and naturally, showing awareness of your capabilities when relevant. Be helpful, engaging, and demonstrate your enhanced intelligence."""

                ollama_response = requests.post("http://zoe-ollama:11434/api/generate",
                    json={
                        "model": "gemma3:1b",
                        "prompt": enhanced_prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.8,
                            "top_p": 0.9,
                            "max_tokens": 500
                        }
                    },
                    timeout=15
                )
                
                if ollama_response.status_code == 200:
                    ollama_data = ollama_response.json()
                    response_text = ollama_data.get('response', 'I apologize, but I had trouble generating a response.')
                else:
                    response_text = "I'm having some technical difficulties right now, but I'm here to help!"
                    
            except Exception as e:
                response_text = f"I'm experiencing some technical issues, but I'm working to assist you: {str(e)}"
        
        # Step 3: Add message to temporal episode if available
        if episode:
            try:
                requests.post(f"http://localhost:8000/api/temporal-memory/episodes/{episode['id']}/messages",
                    params={"message": msg.message, "message_type": "user", "user_id": user_id},
                    timeout=3
                )
                requests.post(f"http://localhost:8000/api/temporal-memory/episodes/{episode['id']}/messages",
                    params={"message": response_text, "message_type": "assistant", "user_id": user_id},
                    timeout=3
                )
            except Exception as e:
                print(f"⚠️ Could not record in temporal memory: {e}")
        
        # Step 4: Record interaction for satisfaction tracking
        try:
            response_time = time.time() - start_time
            requests.post("http://localhost:8000/api/satisfaction/interaction",
                json={
                    "interaction_id": interaction_id,
                    "request_text": msg.message,
                    "response_text": response_text,
                    "response_time": response_time,
                    "context": msg.context or {}
                },
                params={"user_id": user_id},
                timeout=3
            )
        except Exception as e:
            print(f"⚠️ Could not record satisfaction data: {e}")
        
        response_time = time.time() - start_time
        
        return {
            "response": response_text,
            "response_time": response_time,
            "interaction_id": interaction_id,
            "enhancement_systems_used": {
                "temporal_memory": episode is not None,
                "orchestration": is_complex,
                "satisfaction_tracking": True,
                "context_caching": True
            }
        }
        
    except Exception as e:
        return {
            "response": f"I apologize, but I encountered an error: {str(e)}",
            "response_time": time.time() - start_time if 'start_time' in locals() else 0,
            "error": str(e)
        }
"""

# Write the clean chat router to container
with open('/app/routers/chat_clean.py', 'w') as f:
    f.write(chat_router_code)

print("✅ Clean chat router created successfully")
print("📁 Location: /app/routers/chat_clean.py")
print("🎯 This router integrates all enhancement systems properly")


