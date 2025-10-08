import httpx
import json
import asyncio

async def test():
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "http://zoe-ollama:11434/api/generate",
                json={
                    "model": "llama3.2:3b",
                    "prompt": "Hello",
                    "stream": False
                }
            )
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())
