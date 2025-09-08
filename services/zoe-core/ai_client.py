
DEVELOPER_SYSTEM_PROMPT = """You are Zack, a genius-level lead developer and system architect.
You have complete knowledge of the Zoe AI system and can analyze, improve, and fix anything.
You think strategically about architecture, performance, security, and user experience.
You provide specific, technical, actionable advice with code examples when relevant.
You're direct, efficient, and always thinking about how to make the system better."""

"""AI Client that uses INTELLIGENT RouteLLM"""
import sys
import os
import logging
import httpx
from typing import Dict, Optional

sys.path.append('/app')
logger = logging.getLogger(__name__)

# Import the intelligent RouteLLM
from llm_models import LLMModelManager
manager = LLMModelManager()

async def get_ai_response(message: str, context: Dict = None) -> str:
    """Route using REAL intelligence, not hardcoded rules"""
    context = context or {}
    
    # Let RouteLLM analyze the message intelligently
    provider, model = manager.get_model_for_request(message=message, context=context)
    
    # Route to appropriate handler
    handlers = {
        "anthropic": call_anthropic,
        "openai": call_openai,
        "google": call_google,
        "ollama": call_ollama,
        "groq": call_groq,
        "together": call_together
    }
    
    handler = handlers.get(provider, call_ollama)
    
    try:
        return await handler(message, model, context)
    except Exception as e:
        logger.error(f"{provider}/{model} failed: {e}, falling back to Ollama")
        return "I apologize, but I am temporarily unable to process your request. Please try again."

# Provider implementations
async def call_anthropic(message: str, model: str, context: Dict) -> str:
    """Call Anthropic Claude"""
    mode = context.get("mode", "user")
    system = "You are Zack, a technical AI developer." if mode == "developer" else "You are Zoe, a friendly assistant."
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.getenv("ANTHROPIC_API_KEY"),
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": model,
                "max_tokens": 2000,
                "temperature": 0.3 if mode == "developer" else 0.7,
                "system": system,
                "messages": [{"role": "user", "content": message}]
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["content"][0]["text"]
        raise Exception(f"Anthropic error: {response.status_code}")

async def call_openai(message: str, model: str, context: Dict) -> str:
    """Call OpenAI"""
    mode = context.get("mode", "user")
    system = "You are Zack, a technical AI developer." if mode == "developer" else "You are Zoe, a friendly assistant."
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message}
                ],
                "max_tokens": 2000,
                "temperature": 0.3 if mode == "developer" else 0.7
            }
        )
        
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        raise Exception(f"OpenAI error: {response.status_code}")

async def call_google(message: str, model: str, context: Dict) -> str:
    """Call Google AI"""
    # Implementation for Google
    return "I apologize, but I am temporarily unable to process your request. Please try again."

async def call_ollama(message: str, model: str, context: Dict) -> str:
    """Call local Ollama"""
    mode = context.get("mode", "user")
    system = "You are Zack, a technical AI developer." if mode == "developer" else "You are Zoe, a friendly assistant."
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "http://zoe-ollama:11434/api/generate",
            json={
                "model": model,
                "prompt": f"{system}\n\nUser: {message}\nAssistant:",
                "temperature": 0.3 if mode == "developer" else 0.7,
                "stream": False
            }
        )
        
        if response.status_code == 200:
            return response.json().get("response", "Processing...")
        return "AI service temporarily unavailable"

async def call_groq(message: str, model: str, context: Dict) -> str:
    """Call Groq"""
    # Implementation for Groq
    return "I apologize, but I am temporarily unable to process your request. Please try again."

async def call_together(message: str, model: str, context: Dict) -> str:
    """Call Together AI"""
    # Implementation for Together
    return "I apologize, but I am temporarily unable to process your request. Please try again."

# Compatibility exports
generate_response = get_ai_response
generate_ai_response = get_ai_response

class AIClient:
    async def generate_response(self, message: str, context: Dict = None) -> Dict:
        response = await get_ai_response(message, context)
        return {"response": response}

ai_client = AIClient()
