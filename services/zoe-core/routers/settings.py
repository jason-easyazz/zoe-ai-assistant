from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
import sys
sys.path.append('/app')
from config.api_keys import api_keys

router = APIRouter(prefix="/api/settings")

class APIKeyUpdate(BaseModel):
    service: str
    key: str

@router.get("/apikeys")
async def list_keys():
    return api_keys.list_keys()

@router.post("/apikeys")
async def save_key(update: APIKeyUpdate):
    if update.service == "openai" and not update.key.startswith("sk-"):
        raise HTTPException(400, "Invalid OpenAI key format")
    if update.service == "anthropic" and not update.key.startswith("sk-ant-"):
        raise HTTPException(400, "Invalid Anthropic key format")
    
    api_keys.set_key(update.service, update.key)
    return {"status": "saved"}

@router.delete("/apikeys/{service}")
async def remove_key(service: str):
    if api_keys.remove_key(service):
        return {"status": "removed"}
    raise HTTPException(404, "Not found")

@router.post("/apikeys/{service}/test")
async def test_key(service: str):
    key = api_keys.get_key(service)
    if not key:
        return {"valid": False, "error": "No key configured"}
    
    try:
        if service == "openai":
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=5.0
                )
                return {"valid": resp.status_code == 200}
        
        elif service == "anthropic":
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01"
                    },
                    json={
                        "model": "claude-3-haiku-20240307",
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "test"}]
                    },
                    timeout=5.0
                )
                return {"valid": resp.status_code in [200, 201]}
    except Exception as e:
        return {"valid": False, "error": str(e)}
    
    return {"valid": False}
