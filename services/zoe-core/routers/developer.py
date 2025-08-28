"""Practical Developer Router"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import sys
sys.path.append('/app')
from ai_client import get_ai_response

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    """Zack - Developer Assistant"""
    
    # Direct pass-through to AI with developer context
    response = await get_ai_response(msg.message, {"mode": "developer"})
    return {"response": response}

@router.get("/status")
async def status():
    return {"api": "online"}
