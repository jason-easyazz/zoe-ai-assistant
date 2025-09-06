from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import sys
sys.path.append("/app")

router = APIRouter(prefix="/api/developer", tags=["developer"])

class ChatMessage(BaseModel):
    message: str

@router.get("/status")
async def status():
    return {"status": "autonomous"}

@router.get("/awareness") 
async def awareness():
    files = subprocess.run("ls /app/", shell=True, capture_output=True, text=True)
    return {"files": files.stdout}

@router.post("/chat")
async def chat(msg: ChatMessage):
    from ai_client_complete import get_ai_response
    response = await get_ai_response(f"You are Zack. User: {msg.message}", {"mode": "developer"})
    return {"response": response}
