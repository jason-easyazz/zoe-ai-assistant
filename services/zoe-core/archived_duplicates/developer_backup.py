from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import sqlite3
import sys
sys.path.append("/app")

router = APIRouter(prefix="/api/developer", tags=["developer"])

class ChatMessage(BaseModel):
    message: str

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    # Only gather what the query needs
    query_lower = msg.message.lower()
    
    context_parts = ["You are Zack, the autonomous developer inside Zoe."]
    
    # Add relevant context based on query
    if "database" in query_lower or "table" in query_lower:
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type=\"table\"")
        tables = [t[0] for t in cursor.fetchall()]
        conn.close()
        context_parts.append(f"Database tables: {tables}")
    
    if "router" in query_lower or "endpoint" in query_lower:
        routers = subprocess.run("ls /app/routers/*.py", shell=True, capture_output=True, text=True)
        context_parts.append(f"Routers: {routers.stdout[:200]}")
    
    if "file" in query_lower or "python" in query_lower:
        count = subprocess.run("find /app -name \"*.py\" | wc -l", shell=True, capture_output=True, text=True)
        context_parts.append(f"I can see {count.stdout.strip()} Python files")
    
    # Build focused context
    context = " ".join(context_parts) + f"\n\nUser: {msg.message}"
    
    # Get response without timeout issues
    from ai_client_complete import get_ai_response
    response = await get_ai_response(context, {"mode": "developer"})
    
    return {"response": response}

@router.get("/status")
async def status():
    return {"status": "autonomous", "mode": "developer"}

@router.get("/awareness")
async def awareness():
    result = subprocess.run("ls -la /app/", shell=True, capture_output=True, text=True)
    return {"visibility": result.stdout}
