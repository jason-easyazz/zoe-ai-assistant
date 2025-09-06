"""
Dynamic LLM Model Management System
Discovers available models for each provider and stores in config
"""
import os
import json
import httpx
from typing import Dict, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class LLMModelManager:
    """Manages LLM models across all providers"""
    
    def __init__(self):
        self.config_file = "/app/data/llm_models.json"
        self.models = self.load_config()
        self.available_models = {}
        
    def load_config(self) -> Dict:
        """Load saved model configuration"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        
        # Default configuration
        return {
            "providers": {
                "openai": {
                    "enabled": False,
                    "models": [],
                    "default": None,
                    "api_key_var": "OPENAI_API_KEY"
                },
                "anthropic": {
                    "enabled": False,
                    "models": [],
                    "default": None,
                    "api_key_var": "ANTHROPIC_API_KEY"
                },
                "google": {
                    "enabled": False,
                    "models": [],
                    "default": None,
                    "api_key_var": "GOOGLE_API_KEY"
                },
                "mistral": {
                    "enabled": False,
                    "models": [],
                    "default": None,
                    "api_key_var": "MISTRAL_API_KEY"
                },
                "cohere": {
                    "enabled": False,
                    "models": [],
                    "default": None,
                    "api_key_var": "COHERE_API_KEY"
                },
                "groq": {
                    "enabled": False,
                    "models": [],
                    "default": None,
                    "api_key_var": "GROQ_API_KEY"
                },
                "ollama": {
                    "enabled": True,
                    "models": [],
                    "default": "llama3.2:3b",
                    "api_key_var": None,
                    "base_url": "http://zoe-ollama:11434"
                }
            },
            "default_provider": "ollama",
            "last_updated": None
        }
    
    def save_config(self):
        """Save configuration to file"""
        self.models["last_updated"] = datetime.now().isoformat()
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        with open(self.config_file, 'w') as f:
            json.dump(self.models, f, indent=2)
    
    async def discover_openai_models(self, api_key: str) -> List[str]:
        """Discover available OpenAI models"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    # Filter for chat models
                    models = [m["id"] for m in data["data"] 
                             if "gpt" in m["id"].lower()]
                    
                    # Common models in preferred order
                    preferred = ["gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"]
                    available = [m for m in preferred if m in models]
                    
                    return available if available else models[:5]
        except Exception as e:
            logger.error(f"OpenAI discovery failed: {e}")
        return []
    
    async def discover_anthropic_models(self, api_key: str) -> List[str]:
        """Discover available Anthropic models"""
        # Anthropic doesn't have a models endpoint, so we test known models
        known_models = [
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
            "claude-3-haiku-20240307",
            "claude-2.1",
            "claude-instant-1.2"
        ]
        
        working_models = []
        async with httpx.AsyncClient() as client:
            for model in known_models:
                try:
                    # Test with a minimal request
                    response = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01"
                        },
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": "Hi"}],
                            "max_tokens": 1
                        },
                        timeout=5
                    )
                    if response.status_code in [200, 400]:  # 400 means model exists but request was minimal
                        working_models.append(model)
                        logger.info(f"✓ Anthropic model available: {model}")
                except:
                    pass
        
        return working_models
    
    async def discover_google_models(self, api_key: str) -> List[str]:
        """Discover available Google models"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    return [m["name"].split("/")[-1] for m in data.get("models", [])]
        except Exception as e:
            logger.error(f"Google discovery failed: {e}")
        return []
    
    async def discover_ollama_models(self) -> List[str]:
        """Discover available Ollama models"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "http://zoe-ollama:11434/api/tags",
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.error(f"Ollama discovery failed: {e}")
        return []
    
    async def discover_all_models(self):
        """Discover models for all configured providers"""
        logger.info("🔍 Starting model discovery...")
        
        # Check each provider
        for provider, config in self.models["providers"].items():
            api_key_var = config.get("api_key_var")
            
            if api_key_var:
                api_key = os.getenv(api_key_var, "").strip()
                if not api_key or api_key == "your-key-here":
                    continue
            
            logger.info(f"Checking {provider}...")
            
            if provider == "openai":
                models = await self.discover_openai_models(api_key)
            elif provider == "anthropic":
                models = await self.discover_anthropic_models(api_key)
            elif provider == "google":
                models = await self.discover_google_models(api_key)
            elif provider == "ollama":
                models = await self.discover_ollama_models()
            else:
                models = []
            
            if models:
                self.models["providers"][provider]["enabled"] = True
                self.models["providers"][provider]["models"] = models
                self.models["providers"][provider]["default"] = models[0]
                logger.info(f"✅ {provider}: Found {len(models)} models")
            else:
                self.models["providers"][provider]["enabled"] = False
                logger.info(f"❌ {provider}: No models available")
        
        # Set default provider to first enabled one
        for provider, config in self.models["providers"].items():
            if config["enabled"]:
                self.models["default_provider"] = provider
                break
        
        self.save_config()
        logger.info(f"✅ Model discovery complete. Default: {self.models['default_provider']}")
    
    def get_model_for_request(self, 
                            provider: Optional[str] = None,
                            complexity: str = "medium") -> tuple[str, str]:
        """
        Get the best model for a request
        Returns: (provider, model_name)
        """
        # Use specified provider or default
        if not provider:
            provider = self.models["default_provider"]
        
        provider_config = self.models["providers"].get(provider)
        
        if not provider_config or not provider_config["enabled"]:
            # Fallback to any available provider
            for p, config in self.models["providers"].items():
                if config["enabled"]:
                    provider = p
                    provider_config = config
                    break
            else:
                return None, None
        
        # Select model based on complexity
        models = provider_config["models"]
        if not models:
            return None, None
        
        # For high complexity, use the best (usually first) model
        # For low complexity, use a smaller/cheaper model if available
        if complexity == "high" and models:
            return provider, models[0]
        elif complexity == "low" and len(models) > 1:
            return provider, models[-1]  # Usually smaller models are listed last
        else:
            return provider, provider_config["default"]
    
    def get_ui_config(self) -> Dict:
        """Get configuration for UI settings page"""
        ui_config = {
            "providers": [],
            "default_provider": self.models["default_provider"]
        }
        
        for provider, config in self.models["providers"].items():
            ui_config["providers"].append({
                "name": provider,
                "enabled": config["enabled"],
                "models": config["models"],
                "default_model": config["default"],
                "has_key": bool(os.getenv(config.get("api_key_var", ""), "").strip())
                           if config.get("api_key_var") else True
            })
        
        return ui_config
    
    async def update_settings(self, settings: Dict):
        """Update settings from UI"""
        if "default_provider" in settings:
            self.models["default_provider"] = settings["default_provider"]
        
        for provider_update in settings.get("providers", []):
            provider = provider_update["name"]
            if provider in self.models["providers"]:
                if "default_model" in provider_update:
                    self.models["providers"][provider]["default"] = provider_update["default_model"]
        
        self.save_config()
        return {"status": "success"}

# Global instance
model_manager = LLMModelManager()
