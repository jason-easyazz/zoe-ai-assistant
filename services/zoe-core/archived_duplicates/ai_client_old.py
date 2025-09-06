"""Fixed AI Client with proper API integration"""
import httpx
import os
import logging
import json

logger = logging.getLogger(__name__)

async def get_ai_response(message: str, context: dict = None) -> str:
    """Get AI response with proper API handling"""
    
    context = context or {}
    mode = context.get("mode", "user")
    
    # Check complexity
    is_complex = len(message.split()) > 15
    
    # For complex queries, try APIs first
    if is_complex:
        # Try OpenAI
        if os.getenv("OPENAI_API_KEY"):
            try:
                logger.info("Trying OpenAI API...")
                response = await call_openai(message, mode)
                if response:
                    return response
            except Exception as e:
                logger.error(f"OpenAI failed: {e}")
        
        # Try Anthropic
        if os.getenv("ANTHROPIC_API_KEY"):
            try:
                logger.info("Trying Anthropic API...")
                response = await call_anthropic(message, mode)
                if response:
                    return response
            except Exception as e:
                logger.error(f"Anthropic failed: {e}")
    
    # Fallback to Ollama
    logger.info("Using local Ollama")
    return await call_ollama(message, mode)

async def call_openai(message: str, mode: str) -> str:
    """Call OpenAI API"""
    api_key = os.getenv("OPENAI_API_KEY")
    
    if mode == "developer":
        system = "You are Zack, a technical AI developer. Provide detailed technical solutions and code."
        model = "gpt-4-turbo-preview"  # Use GPT-4 for complex dev tasks
    else:
        system = "You are Zoe, a friendly AI assistant."
        model = "gpt-3.5-turbo"
    
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message}
                ],
                "max_tokens": 1000,
                "temperature": 0.3 if mode == "developer" else 0.7
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        else:
            raise Exception(f"OpenAI API error: {response.status_code} - {response.text}")

async def call_anthropic(message: str, mode: str) -> str:
    """Call Anthropic API"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    
    if mode == "developer":
        system = "You are Zack, a technical AI developer. Provide detailed technical solutions and code."
    else:
        system = "You are Zoe, a friendly AI assistant."
    
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-3-haiku-20240307",
                "max_tokens": 1000,
                "system": system,
                "messages": [
                    {"role": "user", "content": message}
                ]
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["content"][0]["text"]
        else:
            raise Exception(f"Anthropic API error: {response.status_code}")

async def call_ollama(message: str, mode: str) -> str:
    """Call local Ollama"""
    if mode == "developer":
        prompt = f"You are Zack, a technical developer. User: {message}\nZack:"
    else:
        prompt = f"You are Zoe, a friendly assistant. User: {message}\nZoe:"
    
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            "http://zoe-ollama:11434/api/generate",
            json={
                "model": "llama3.2:3b",
                "prompt": prompt,
                "stream": False
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get("response", "No response from Ollama")
        else:
            return "Local model unavailable"
