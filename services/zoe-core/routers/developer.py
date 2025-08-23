from fastapi import APIRouter
from pydantic import BaseModel
import sys
sys.path.append('/app')
from ai_client import get_ai_response
from config.api_keys import api_keys

router = APIRouter(prefix="/api/developer")

class DevChat(BaseModel):
    message: str

@router.post("/chat")
async def developer_chat(msg: DevChat):
    response = await get_ai_response(msg.message, temperature=0.3)
    
    # Determine which model was actually used
    if api_keys.get_key("anthropic"):
        model = "claude"
    elif api_keys.get_key("openai"):
        model = "gpt-4"
    else:
        model = "ollama"
    
    return {"response": response, "model": model}

@router.get("/status")
async def status():
    has_keys = bool(api_keys.get_key("anthropic") or api_keys.get_key("openai"))
    return {
        "status": "online",
        "claude_available": has_keys,
        "keys_loaded": {
            "openai": bool(api_keys.get_key("openai")),
            "anthropic": bool(api_keys.get_key("anthropic"))
        }
    }
