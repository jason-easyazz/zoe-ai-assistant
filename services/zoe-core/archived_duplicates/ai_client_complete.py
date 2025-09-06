"""Complete AI Client with all providers"""
import os
import sys
import httpx
import json
import logging
from typing import Dict, Optional

sys.path.append('/app')
from llm_models import LLMModelManager

logger = logging.getLogger(__name__)
manager = LLMModelManager()

async def get_ai_response(message: str, context: Dict = None) -> str:
    """Route to best available provider from discovery"""
    
    context = context or {}
    mode = context.get("mode", "user")
    complexity = "high" if len(message.split()) > 15 else "medium"
    
    # Get best model from discovery
    provider, model = manager.get_model_for_request(complexity=complexity)
    logger.info(f"Selected: {provider}/{model}")
    
    # Set personality
    system = "You are Zack, a technical AI developer." if mode == "developer" else "You are Zoe, a friendly AI assistant."
    temperature = 0.3 if mode == "developer" else 0.7
    
    # Route to provider
    providers = {
        "openai": call_openai,
        "anthropic": call_anthropic,
        "google": call_google,
        "mistral": call_mistral,
        "cohere": call_cohere,
        "groq": call_groq,
        "together": call_together,
        "perplexity": call_perplexity,
        "ollama": call_ollama
    }
    
    handler = providers.get(provider, call_ollama)
    
    try:
        return await handler(message, system, model, temperature)
    except Exception as e:
        logger.error(f"{provider} failed: {e}, falling back")
        return await call_ollama(message, system, "llama3.2:3b", temperature)

# OpenAI
async def call_openai(message: str, system: str, model: str, temperature: float) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message}
                ],
                "temperature": temperature,
                "max_tokens": 1000
            }
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        raise Exception(f"OpenAI error: {resp.status_code}")

# Anthropic
async def call_anthropic(message: str, system: str, model: str, temperature: float) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.getenv("ANTHROPIC_API_KEY"),
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": model,
                "max_tokens": 1000,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": message}]
            }
        )
        if resp.status_code == 200:
            return resp.json()["content"][0]["text"]
        raise Exception(f"Anthropic error: {resp.status_code}")

# Google Gemini
async def call_google(message: str, system: str, model: str, temperature: float) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": os.getenv("GOOGLE_API_KEY")},
            json={
                "contents": [{"parts": [{"text": f"{system}\n\n{message}"}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": 1000}
            }
        )
        if resp.status_code == 200:
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        raise Exception(f"Google error: {resp.status_code}")

# Mistral
async def call_mistral(message: str, system: str, model: str, temperature: float) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('MISTRAL_API_KEY')}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message}
                ],
                "temperature": temperature,
                "max_tokens": 1000
            }
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        raise Exception(f"Mistral error: {resp.status_code}")

# Cohere
async def call_cohere(message: str, system: str, model: str, temperature: float) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.cohere.ai/v1/generate",
            headers={
                "Authorization": f"Bearer {os.getenv('COHERE_API_KEY')}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "prompt": f"{system}\n\n{message}",
                "temperature": temperature,
                "max_tokens": 1000
            }
        )
        if resp.status_code == 200:
            return resp.json()["generations"][0]["text"]
        raise Exception(f"Cohere error: {resp.status_code}")

# Groq
async def call_groq(message: str, system: str, model: str, temperature: float) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message}
                ],
                "temperature": temperature,
                "max_tokens": 1000
            }
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        raise Exception(f"Groq error: {resp.status_code}")

# Together
async def call_together(message: str, system: str, model: str, temperature: float) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.together.xyz/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('TOGETHER_API_KEY')}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message}
                ],
                "temperature": temperature,
                "max_tokens": 1000
            }
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        raise Exception(f"Together error: {resp.status_code}")

# Perplexity
async def call_perplexity(message: str, system: str, model: str, temperature: float) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('PERPLEXITY_API_KEY')}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message}
                ],
                "temperature": temperature,
                "max_tokens": 1000
            }
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        raise Exception(f"Perplexity error: {resp.status_code}")

# Ollama (local)
async def call_ollama(message: str, system: str, model: str, temperature: float) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "http://zoe-ollama:11434/api/generate",
            json={
                "model": model,
                "prompt": f"{system}\n\nUser: {message}\nAssistant:",
                "temperature": temperature,
                "stream": False,
                "options": {"num_predict": 500}
            }
        )
        if resp.status_code == 200:
            return resp.json().get("response", "")
        return "Unable to generate response"
