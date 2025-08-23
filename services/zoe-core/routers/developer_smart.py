"""Smart developer router - executes then lets LLM format"""
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
    """Execute commands and let LLM format the response"""
    message = msg.message.lower()
    
    # Determine what commands to run based on request
    commands_to_run = []
    
    if any(word in message for word in ['docker', 'container', 'running', 'services']):
        commands_to_run.append(("docker ps", "Docker containers"))
    
    if any(word in message for word in ['health', 'status', 'check', 'system']):
        commands_to_run.append(("docker ps --format 'table {{.Names}}\t{{.Status}}'", "Container status"))
        commands_to_run.append(("free -h", "Memory usage"))
        commands_to_run.append(("df -h /", "Disk usage"))
    
    if 'memory' in message or 'ram' in message:
        commands_to_run.append(("free -h", "Memory info"))
    
    if 'disk' in message or 'storage' in message:
        commands_to_run.append(("df -h", "Disk space"))
    
    if any(word in message for word in ['log', 'error', 'problem']):
        commands_to_run.append(("docker logs zoe-core --tail 10 2>&1 | grep -i error || echo 'No errors found'", "Recent errors"))
    
    # Execute commands and collect results
    if commands_to_run:
        results = []
        for cmd, description in commands_to_run:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd="/app")
            results.append(f"{description}:\n{result.stdout}")
        
        # Send to LLM for human-friendly formatting
        raw_data = "\n\n".join(results)
        
        llm_prompt = f"""The user asked: "{msg.message}"

I executed these system commands and got this data:

{raw_data}

Please explain this data in a friendly, conversational way. Use emojis, formatting, and helpful context. 
Point out anything important. Be concise but informative."""

        # Get LLM to format nicely
        response = await ai_client.generate_response(llm_prompt, {"mode": "assistant"})
        
        return {"response": response["response"]}
    
    # For unknown requests, ask LLM directly
    response = await ai_client.generate_response(
        f"You're Claude, the Zoe system developer assistant. The user said: {msg.message}. Help them with system management.",
        {"mode": "developer"}
    )
    
    return {"response": response["response"]}

@router.get("/status")
async def status():
    return {"status": "online", "smart_mode": True}
