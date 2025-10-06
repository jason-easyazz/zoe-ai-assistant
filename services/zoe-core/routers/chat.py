"""Fixed Chat Router for Zoe"""
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional
import sys
sys.path.append('/app')
from ai_client import get_ai_response

router = APIRouter(tags=["chat"])

class ChatMessage(BaseModel):
    message: str
    context: Optional[dict] = None

@router.post("/api/chat/")
@router.post("/api/chat")
async def chat(msg: ChatMessage, user_id: str = Query("default", description="User ID for privacy isolation")):
    """Handle user chat messages (Zoe) with self-awareness"""
    try:
        import time
        start_time = time.time()
        
        # Enhanced context with self-awareness and user isolation
        context = {
            "mode": "user",
            "user_id": user_id,  # Critical for privacy isolation
            "response_time": 0.0,  # Will be updated after response
            "user_satisfaction": 0.5,  # Default, can be updated based on user feedback
            "complexity": "medium",  # Will be determined by RouteLLM
            "active_tasks": 0,  # Can be enhanced with actual task count
            "familiarity": "medium"  # Can be enhanced with user interaction history
        }
        
        # Merge with provided context
        if msg.context:
            context.update(msg.context)
        
        # Get AI response with self-awareness integration
        response = await get_ai_response(msg.message, context)
        
        # Update response time
        response_time = time.time() - start_time
        context["response_time"] = response_time
        
        return {"response": response, "response_time": response_time}
    except Exception as e:
        # Log the actual error
        import logging
        logging.error(f"Chat error: {str(e)}")
        # Return the error for debugging
        return {"response": f"Error: {str(e)}"}
