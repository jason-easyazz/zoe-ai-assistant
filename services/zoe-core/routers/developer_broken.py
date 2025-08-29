from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import sqlite3
import os

router = APIRouter(prefix="/api/developer", tags=["developer"])

class ChatMessage(BaseModel):
    message: str

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    # Gather REAL system info
    system_data = {}
    
    # Get Python files
    py_files = subprocess.run("find /app -name \"*.py\" -type f", shell=True, capture_output=True, text=True)
    system_data["python_files"] = py_files.stdout.strip().split("\n")
    
    # Get database tables
    try:
        conn = sqlite3.connect("/app/data/zoe.db")
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type=\"table\"")
        system_data["tables"] = [t[0] for t in cursor.fetchall()]
        conn.close()
    except:
        system_data["tables"] = []
    
    # Get router files
    router_files = subprocess.run("ls /app/routers/*.py", shell=True, capture_output=True, text=True)
    system_data["routers"] = router_files.stdout.strip().split("\n")
    
    # Try to use AI, but have fallback
    try:
        import sys
        sys.path.append("/app")
        from ai_client import get_ai_response
        
        context = f"You are Zack. System: {len(system_data[\"python_files\"])} Python files, {len(system_data[\"tables\"])} tables. User: {msg.message}"
        response = await get_ai_response(context, {"mode": "developer"})
    except:
        # Direct response without AI
        response = f"I can see {len(system_data[\"python_files\"])} Python files including {system_data[\"routers\"][:3]}. Database has tables: {system_data[\"tables\"]}. For \"{msg.message}\", I would analyze these components and provide a solution."
    
    return {
        "response": response,
        "system_awareness": {
            "files": len(system_data["python_files"]),
            "tables": system_data["tables"],
            "routers": len(system_data["routers"])
        }
    }

@router.get("/status")
async def status():
    return {"status": "autonomous", "mode": "developer"}

@router.get("/awareness")
async def awareness():
    # Show what Zack can actually see
    result = subprocess.run("ls -la /app/", shell=True, capture_output=True, text=True)
    return {"visibility": result.stdout}
