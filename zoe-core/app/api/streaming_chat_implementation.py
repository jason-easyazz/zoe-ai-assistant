import os
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import httpx

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "ollama:11434")

router = APIRouter(prefix="/api/chat", tags=["chat"])

async def ollama_stream(prompt: str):
    url = f"http://{OLLAMA_HOST}/api/generate"
    payload = {"model": "mistral:latest", "prompt": prompt, "stream": True}
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, json=payload) as resp:
            async for line in resp.aiter_lines():
                if line:
                    data = json.loads(line)
                    token = data.get("response", "")
                    if token:
                        yield token
    yield "[DONE]"

@router.get("/stream")
async def stream(prompt: str):
    async def event_generator():
        async for token in ollama_stream(prompt):
            yield f"data: {token}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
