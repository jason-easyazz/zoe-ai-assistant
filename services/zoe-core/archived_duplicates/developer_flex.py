"""Completely flexible - no hardcoding"""
from fastapi import APIRouter
from pydantic import BaseModel
import subprocess
import sys
sys.path.append('/app')
from ai_client import ai_client

router = APIRouter(prefix="/api/developer")

class ChatMessage(BaseModel):
    message: str

@router.post("/chat")
async def developer_chat(msg: ChatMessage):
    # Step 1: Figure out what commands to run
    cmd_prompt = f"List Linux commands to answer: {msg.message}\nOutput only commands, max 5 lines."
    
    cmd_response = await ai_client.generate_response(cmd_prompt, {"mode": "assistant", "temperature": 0})
    commands = [c.strip() for c in cmd_response["response"].split('\n') if c.strip()][:5]
    
    # Step 2: Run them
    results = []
    for cmd in commands:
        try:
            output = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3, cwd="/app")
            results.append(output.stdout[:500])
        except:
            pass
    
    # Step 3: Format response
    format_prompt = f"""Question: {msg.message}
Data: {' '.join(results)}

Create HTML response:
- Use <h4> for headers (color:#1e40af)
- Use <div> for content
- 8-12 lines total
- Include numbers and context
- Executive style - professional but readable
- End with brief assessment

Just create appropriate HTML for whatever was asked."""

    response = await ai_client.generate_response(format_prompt, {"mode": "assistant", "temperature": 0.2})
    return {"response": response["response"]}

@router.get("/status")
async def status():
    return {"status": "online"}
