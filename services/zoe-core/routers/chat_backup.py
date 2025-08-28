from fastapi import APIRouter
from pydantic import BaseModel
import sys
sys.path.append('/app')
from ai_client import get_ai_response

router = APIRouter(prefix="/api/chat")

class ChatMessage(BaseModel):
    message: str

@router.post("/")
async def chat(msg: ChatMessage):
    response = await get_ai_response(msg.message)
    return {"response": response}
