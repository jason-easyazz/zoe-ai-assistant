"""Fixed Chat Router for Zoe"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List, Dict
import sys
sys.path.append('/app')
from ai_client import get_ai_response

router = APIRouter(tags=["chat"])

# In-memory conversation storage (in production, use Redis or database)
conversation_history = {}

class ChatMessage(BaseModel):
    message: str
    context: Optional[dict] = None
    session_id: Optional[str] = "default"

class ChatResponse(BaseModel):
    response: str
    session_id: str

@router.post("/api/chat/")
@router.post("/api/chat")
async def chat(msg: ChatMessage):
    """Handle user chat messages (Zoe) with conversation context"""
    try:
        session_id = msg.session_id or "default"
        
        # Get or create conversation history for this session
        if session_id not in conversation_history:
            conversation_history[session_id] = []
        
        # Add user message to history
        conversation_history[session_id].append({
            "role": "user",
            "content": msg.message
        })
        
        # Prepare context with conversation history
        context = {
            "mode": "user",
            "conversation_history": conversation_history[session_id],
            "session_id": session_id
        }
        
        # Get AI response
        response = await get_ai_response(msg.message, context=context)
        
        # Add AI response to history
        conversation_history[session_id].append({
            "role": "assistant", 
            "content": response
        })
        
        # Keep only last 20 messages to prevent memory issues
        if len(conversation_history[session_id]) > 20:
            conversation_history[session_id] = conversation_history[session_id][-20:]
        
        return ChatResponse(response=response, session_id=session_id)
        
    except Exception as e:
        # Log the actual error
        import logging
        logging.error(f"Chat error: {str(e)}")
        # Return the error for debugging
        return ChatResponse(response=f"Error: {str(e)}", session_id=msg.session_id or "default")
