from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import sys
sys.path.append("/app")
sys.path.append("/app/routers")

router = APIRouter(prefix="/api/simple", tags=["Simple AI"])

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

@router.post("/chat")
async def simple_chat(request: ChatRequest):
    from chat_sessions import get_or_create_session
    from dynamic_router import dynamic_router
    
    session = get_or_create_session(request.session_id)
    session.add_message("user", request.message)
    
    # Simple response
    response = f"I understand you need: {request.message}"
    session.extract_requirements(request.message, response)
    
    provider, model = dynamic_router.get_best_model_for_complexity("simple")
    
    return {
        "response": response,
        "session_id": session.session_id,
        "requirements": len(session.extracted_requirements),
        "can_create_task": session.can_create_task(),
        "model": f"{provider}/{model}"
    }
