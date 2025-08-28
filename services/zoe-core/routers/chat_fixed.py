"""Fixed Chat Router for Zoe"""
from fastapi import APIRouter
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
async def chat(msg: ChatMessage):
    """Handle user chat messages (Zoe)"""
    try:
        # This works as proven by direct test
        response = await get_ai_response(msg.message, context={"mode": "user"})
        return {"response": response}
    except Exception as e:
        # Log the actual error
        import logging
        logging.error(f"Chat error: {str(e)}")
        # Return the error for debugging
        return {"response": f"Error: {str(e)}"}
