import httpx
import logging
import sys
sys.path.append('/app')
from config.api_keys import api_keys

logger = logging.getLogger(__name__)

async def get_ai_response(message, context=None, temperature=0.7):
    """Get AI response using saved API keys"""
    
    # Load keys
    anthropic_key = api_keys.get_key("anthropic")
    openai_key = api_keys.get_key("openai")
    
    print(f"DEBUG: Anthropic key exists: {bool(anthropic_key)}")
    print(f"DEBUG: OpenAI key exists: {bool(openai_key)}")
    
    # Try Anthropic first
    if anthropic_key:
        try:
            print("Using Anthropic Claude...")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-3-sonnet-20240229",
                        "max_tokens": 1000,
                        "temperature": temperature,
                        "messages": [{"role": "user", "content": message}]
                    }
                )
                print(f"Anthropic response status: {resp.status_code}")
                if resp.status_code == 200:
                    data = resp.json()
                    return data["content"][0]["text"]
                else:
                    print(f"Anthropic error: {resp.text}")
        except Exception as e:
            print(f"Anthropic exception: {e}")
    
    # Try OpenAI
    if openai_key:
        try:
            print("Using OpenAI GPT-4...")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {openai_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "gpt-4-turbo-preview",
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant."},
                            {"role": "user", "content": message}
                        ],
                        "temperature": temperature,
                        "max_tokens": 1000
                    }
                )
                print(f"OpenAI response status: {resp.status_code}")
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    print(f"OpenAI error: {resp.text}")
        except Exception as e:
            print(f"OpenAI exception: {e}")
    
    # Fallback to Ollama
    print("Falling back to Ollama...")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "http://zoe-ollama:11434/api/generate",
                json={
                    "model": "llama3.2:3b",
                    "prompt": message,
                    "temperature": temperature,
                    "stream": False
                }
            )
            if resp.status_code == 200:
                return resp.json().get("response", "Error")
    except Exception as e:
        print(f"Ollama exception: {e}")
    
    return "All AI services failed"
