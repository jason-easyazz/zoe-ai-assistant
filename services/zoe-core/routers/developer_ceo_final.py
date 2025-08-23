"""CEO style brief with dynamic commands"""
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
    # Decide what commands to run
    decide_prompt = f"""What Linux commands for: {msg.message}
Output ONLY commands, one per line, max 4."""

    decision = await ai_client.generate_response(decide_prompt, {"mode": "assistant", "temperature": 0})
    commands = [cmd.strip() for cmd in decision["response"].split('\n') if cmd.strip()][:4]
    
    # Execute
    data = []
    for cmd in commands:
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd="/app", timeout=3)
            if result.stdout:
                data.append(result.stdout[:200])
        except:
            pass
    
    # CEO-style format
    format_prompt = f"""You're briefing a CEO. User asked: {msg.message}

Raw data: {' '.join(data)}

Create an ULTRA-BRIEF HTML response:
- Maximum 5 lines
- Use status icons: ✅ good, ⚠️ warning, ❌ problem  
- Numbers and percentages only
- NO technical jargon
- NO explanations unless there's a problem

HTML format:
<h4 style="color:#1e40af">Title</h4>
<div><strong>Status:</strong> <span style="color:#22c55e">✅ Good</span></div>
<div>• Metric: Value</div>

Example for system health:
<h4 style="color:#1e40af">System Health</h4>
<div><strong>Status:</strong> <span style="color:#22c55e">✅ Operational</span></div>
<div>• Services: 7/7 running</div>
<div>• Resources: 20% used</div>
<div>• Issues: None</div>

BE BRIEF. CEO has 10 seconds."""

    response = await ai_client.generate_response(format_prompt, {"mode": "assistant", "temperature": 0.1})
    return {"response": response["response"]}

@router.get("/status")
async def status():
    return {"status": "online"}
