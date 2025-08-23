"""Smart AI Client that uses dynamically discovered models"""
import os
import httpx
import json
import logging
import asyncio
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class AIClient:
    def __init__(self):
        self.models = {}
        self.load_discovered_models()
    
    def load_discovered_models(self):
        """Load the discovered models"""
        try:
            with open('/app/data/available_models.json', 'r') as f:
                self.models = json.load(f)
                logger.info(f"Loaded models: {list(self.models.keys())}")
        except:
            self.models = {}
            logger.warning("No discovered models found yet")
    
    async def generate_response(self, message: str, context: Dict = None):
        """Try each provider with their discovered models"""
        
        # Try Anthropic first if available
        if self.models.get('anthropic'):
            api_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
            if api_key and api_key != 'your-key-here':
                model = self.models['anthropic'][0]  # Use first available model
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        response = await client.post(
                            "https://api.anthropic.com/v1/messages",
                            headers={
                                "x-api-key": api_key,
                                "anthropic-version": "2023-06-01"
                            },
                            json={
                                "model": model,
                                "messages": [{"role": "user", "content": message}],
                                "max_tokens": 1000
                            }
                        )
                        if response.status_code == 200:
                            data = response.json()
                            logger.info(f"✅ Using Claude model: {model}")
                            return {
                                "response": data["content"][0]["text"],
                                "model": f"claude/{model}"
                            }
                except Exception as e:
                    logger.error(f"Claude failed: {e}")
        
        # Try OpenAI
        if self.models.get('openai'):
            api_key = os.getenv('OPENAI_API_KEY', '').strip()
            if api_key and api_key != 'your-key-here':
                model = self.models['openai'][0]
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        response = await client.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {api_key}"},
                            json={
                                "model": model,
                                "messages": [{"role": "user", "content": message}],
                                "max_tokens": 1000
                            }
                        )
                        if response.status_code == 200:
                            data = response.json()
                            logger.info(f"✅ Using OpenAI model: {model}")
                            return {
                                "response": data["choices"][0]["message"]["content"],
                                "model": f"openai/{model}"
                            }
                except Exception as e:
                    logger.error(f"OpenAI failed: {e}")
        
        # Try Ollama
        if self.models.get('ollama'):
            model = self.models['ollama'][0]
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        "http://zoe-ollama:11434/api/generate",
                        json={
                            "model": model,
                            "prompt": full_prompt[0] if isinstance(full_prompt, tuple) else full_prompt,
                            "temperature": temp,
                            "stream": False
                        }
                    )
                    if response.status_code == 200:
                        data = response.json()
                        logger.info(f"✅ Using Ollama model: {model}")
                        return {
                            "response": data.get("response", ""),
                            "model": f"ollama/{model}"
                        }
            except Exception as e:
                logger.error(f"Ollama failed: {e}")
        
        return {
            "response": "No AI models available. Please check your API keys and run model discovery.",
            "model": "none"
        }
    
    def get_usage_stats(self):
        return {"models_available": self.models}

ai_client = AIClient()

# Wrapper for compatibility
async def get_ai_response(message: str, **kwargs):
    return await ai_client.generate_response(message, kwargs)
