"""Dynamic Model Discovery - Actually queries the providers"""
import os
import httpx
import json
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class ModelDiscovery:
    def __init__(self):
        self.config_file = "/app/data/available_models.json"
        self.models = {}
        
    async def discover_anthropic_models(self):
        """Test which Claude models actually work with the API key"""
        api_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if not api_key or api_key == 'your-key-here':
            return []
        
        # Claude doesn't have a list endpoint, so test known models
        possible_models = [
            'claude-3-opus-20240229',
            'claude-3-sonnet-20240229',
            'claude-3-haiku-20240307',
            'claude-2.1',
            'claude-2.0',
            'claude-instant-1.2'
        ]
        
        working_models = []
        async with httpx.AsyncClient() as client:
            for model in possible_models:
                try:
                    response = await client.post(
                        'https://api.anthropic.com/v1/messages',
                        headers={
                            'x-api-key': api_key,
                            'anthropic-version': '2023-06-01'
                        },
                        json={
                            'model': model,
                            'messages': [{'role': 'user', 'content': 'test'}],
                            'max_tokens': 1
                        },
                        timeout=5
                    )
                    if response.status_code == 200:
                        working_models.append(model)
                        logger.info(f"✅ Anthropic model available: {model}")
                except:
                    pass
        
        return working_models
    
    async def discover_openai_models(self):
        """Query OpenAI for available models"""
        api_key = os.getenv('OPENAI_API_KEY', '').strip()
        if not api_key or api_key == 'your-key-here':
            return []
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    'https://api.openai.com/v1/models',
                    headers={'Authorization': f'Bearer {api_key}'},
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    # Filter for chat models
                    models = [m['id'] for m in data['data'] 
                             if any(x in m['id'] for x in ['gpt', 'davinci', 'turbo'])]
                    logger.info(f"✅ Found {len(models)} OpenAI models")
                    return sorted(models)
        except Exception as e:
            logger.error(f"OpenAI discovery failed: {e}")
        return []
    
    async def discover_google_models(self):
        """Query Google for available models"""
        api_key = os.getenv('GOOGLE_API_KEY', '').strip()
        if not api_key or api_key == 'your-key-here':
            return []
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f'https://generativelanguage.googleapis.com/v1/models?key={api_key}',
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    models = [m['name'].split('/')[-1] for m in data.get('models', [])]
                    logger.info(f"✅ Found {len(models)} Google models")
                    return models
        except Exception as e:
            logger.error(f"Google discovery failed: {e}")
        return []
    
    async def discover_ollama_models(self):
        """Query Ollama for installed models"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    'http://zoe-ollama:11434/api/tags',
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    models = [m['name'] for m in data.get('models', [])]
                    logger.info(f"✅ Found {len(models)} Ollama models")
                    return models
        except Exception as e:
            logger.error(f"Ollama discovery failed: {e}")
        return []
    
    async def discover_all(self):
        """Discover all available models from all providers"""
        logger.info("🔍 Starting dynamic model discovery...")
        
        self.models = {
            'anthropic': await self.discover_anthropic_models(),
            'openai': await self.discover_openai_models(),
            'google': await self.discover_google_models(),
            'ollama': await self.discover_ollama_models(),
            'last_updated': datetime.now().isoformat()
        }
        
        # Save to file
        with open(self.config_file, 'w') as f:
            json.dump(self.models, f, indent=2)
        
        # Log summary
        for provider, models in self.models.items():
            if provider != 'last_updated' and models:
                logger.info(f"{provider}: {models}")
        
        return self.models
    
    def get_best_model(self, provider=None):
        """Get the best available model"""
        # Load saved models if not in memory
        if not self.models and os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                self.models = json.load(f)
        
        # If specific provider requested
        if provider and provider in self.models:
            models = self.models[provider]
            return models[0] if models else None
        
        # Otherwise return first available from any provider
        for p in ['anthropic', 'openai', 'google', 'ollama']:
            if p in self.models and self.models[p]:
                return self.models[p][0], p
        
        return None, None

# Run discovery on module load
discovery = ModelDiscovery()
