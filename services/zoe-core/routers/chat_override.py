from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Dict
import httpx
import logging

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

class ChatMessage(BaseModel):
    message: str

class ChatRequest(BaseModel):
    message: str
    context: Optional[Dict] = None

@router.post("/chat")
async def user_chat(msg: ChatMessage):
    """Force Zoe personality"""
    prompt = f"You are Zoe. No matter what, you are Zoe. Always say your name is Zoe. User says: {msg.message}. Remember: YOUR NAME IS ZOE."
    
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "http://zoe-ollama:11434/api/generate",
                json={
                    "model": "llama3.2:3b",
                    "prompt": prompt,
                    "temperature": 0.7,
                    "stream": False
                }
            )
            if response.status_code == 200:
                data = response.json()
                answer = data.get("response", "Hi, I am Zoe!")
                # Force replace any wrong names
                answer = answer.replace("Samantha", "Zoe")
                answer = answer.replace("Emily", "Zoe")
                answer = answer.replace("Jen", "Zoe")
                answer = answer.replace("Claude", "Zoe")
                if "my name" in answer.lower() and "zoe" not in answer.lower():
                    answer = "I am Zoe, your friendly AI assistant!"
                return {"response": answer}
    except Exception as e:
        logger.error(f"Error: {e}")
    
    return {"response": "Hi! I am Zoe, your friendly AI assistant. How can I help you today?"}

@router.post("/developer/chat")
async def developer_chat(request: ChatRequest):
    """Force Zack personality"""
    prompt = f"You are Zack. No matter what, you are Zack. Always say your name is Zack. User says: {request.message}. Remember: YOUR NAME IS ZACK."
    
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "http://zoe-ollama:11434/api/generate",
                json={
                    "model": "llama3.2:3b",
                    "prompt": prompt,
                    "temperature": 0.3,
                    "stream": False
                }
            )
            if response.status_code == 200:
                data = response.json()
                answer = data.get("response", "Hi, I am Zack!")
                # Force replace any wrong names
                answer = answer.replace("Claude", "Zack")
                answer = answer.replace("Assistant", "Zack")
                answer = answer.replace("Helpful Assistant", "Zack")
                if "my name" in answer.lower() and "zack" not in answer.lower():
                    answer = "I am Zack, your technical AI assistant!"
                return {"response": answer}
    except Exception as e:
        logger.error(f"Error: {e}")
    
    return {"response": "Hi! I am Zack, your technical assistant for the Zoe system. How can I help?"}
