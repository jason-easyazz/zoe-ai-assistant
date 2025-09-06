"""Give LLM tools to use as it sees fit"""
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
    
    prompt = f"""You are Zack, system administrator for Zoe AI on Raspberry Pi.

YOU HAVE THE ABILITY TO RUN ANY LINUX COMMAND.
To execute a command, write: RUN: command here
You can run multiple commands to gather information.

INSTRUCTIONS:
- When asked about system/health/status, run relevant commands
- When asked about errors, check logs
- When asked about services, check Docker
- For ANY question, run appropriate commands first
- Then format a professional response

FORMATTING:
- Use markdown headers and bullets
- Use emojis for visual status (✅ ⚠️ ❌)
- Keep responses concise and scannable
- Show data, not commands

User request: {msg.message}

Think about what commands you need to run, execute them, then respond professionally."""

    # Let LLM process and potentially request commands
    llm_response = await ai_client.generate_response(prompt, {"mode": "assistant"})
    response_text = llm_response["response"]
    
    # Execute any commands the LLM wants to run
    if "RUN:" in response_text:
        lines = response_text.split('\n')
        final_response = []
        
        for line in lines:
            if line.startswith("RUN:"):
                cmd = line.replace("RUN:", "").strip()
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd="/app")
                # Give result back to LLM to format
                format_prompt = f"Format this data professionally:\nCommand: {cmd}\nOutput: {result.stdout}\n\nCreate a clean summary, not raw output."
                formatted = await ai_client.generate_response(format_prompt, {"mode": "assistant"})
                final_response.append(formatted["response"])
            elif not line.strip().startswith("RUN"):
                final_response.append(line)
        
        return {"response": "\n".join(final_response)}
    
    return {"response": response_text}

@router.get("/status")
async def status():
    return {"status": "online"}
