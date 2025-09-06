"""
INTELLIGENT RouteLLM - Dynamic Model Discovery and Smart Routing
"""
import os
import sys
import json
import httpx
import asyncio
import logging
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)

class LLMModelManager:
    """Intelligent model manager with dynamic discovery and smart routing"""
    
    def __init__(self):
        self.models_file = "/app/data/llm_models.json"
        self.config = self.load_or_discover_models()
        self.usage_stats = {}
        self.model_performance = {}
        
    def load_or_discover_models(self) -> Dict:
        """Load existing config or discover available models"""
        if os.path.exists(self.models_file):
            try:
                with open(self.models_file, 'r') as f:
                    config = json.load(f)
                    # Refresh discovery if older than 24 hours
                    if self._config_age_hours(config) > 24:
                        logger.info("Config older than 24h, refreshing...")
                        return self.discover_all_models_sync()
                    return config
            except:
                pass
        
        return self.discover_all_models_sync()
    
    def _config_age_hours(self, config: Dict) -> float:
        """Check how old the config is"""
        try:
            discovered = config.get('discovered_at', '')
            if discovered:
                discovered_time = datetime.fromisoformat(discovered)
                age = datetime.now() - discovered_time
                return age.total_seconds() / 3600
        except:
            pass
        return 999  # Force refresh if can't determine age
    
    def discover_all_models_sync(self) -> Dict:
        """Synchronous wrapper for discovery"""
        try:
            return asyncio.run(self.discover_all_models())
        except:
            # Fallback config if discovery fails
            return self.get_fallback_config()
    
    async def discover_all_models(self) -> Dict:
        """Discover ALL available models from ALL providers"""
        config = {
            "providers": {},
            "discovered_at": datetime.now().isoformat(),
            "routing_rules": self.get_default_routing_rules()
        }
        
        # Test each provider
        discovery_tasks = [
            self.discover_anthropic_models(),
            self.discover_openai_models(),
            self.discover_google_models(),
            self.discover_ollama_models(),
            self.discover_groq_models(),
            self.discover_together_models()
        ]
        
        results = await asyncio.gather(*discovery_tasks, return_exceptions=True)
        
        # Process results
        for result in results:
            if isinstance(result, dict) and result.get('provider'):
                provider = result['provider']
                config['providers'][provider] = result
                logger.info(f"✅ Discovered {provider}: {len(result.get('models', []))} models")
        
        # Save discovery
        os.makedirs('/app/data', exist_ok=True)
        with open(self.models_file, 'w') as f:
            json.dump(config, f, indent=2)
        
        return config
    
    async def discover_anthropic_models(self) -> Dict:
        """Discover Anthropic Claude models"""
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key or api_key.startswith("your"):
            return {}
        
        models_to_test = [
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
            "claude-2.1",
            "claude-instant-1.2"
        ]
        
        working_models = []
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            for model in models_to_test:
                try:
                    # Quick test to see if model works
                    response = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01"
                        },
                        json={
                            "model": model,
                            "max_tokens": 1,
                            "messages": [{"role": "user", "content": "test"}]
                        }
                    )
                    
                    if response.status_code in [200, 201]:
                        working_models.append(model)
                        # Estimate relative cost (opus=1.0, sonnet=0.5, haiku=0.1)
                        if "opus" in model:
                            cost_factor = 1.0
                        elif "sonnet" in model:
                            cost_factor = 0.5
                        elif "haiku" in model:
                            cost_factor = 0.1
                        else:
                            cost_factor = 0.3
                except:
                    pass
        
        if working_models:
            return {
                "provider": "anthropic",
                "enabled": True,
                "models": working_models,
                "default": working_models[-1],  # Default to cheapest (haiku)
                "api_key_var": "ANTHROPIC_API_KEY",
                "cost_factors": {
                    "opus": 1.0,
                    "sonnet": 0.5,
                    "haiku": 0.1,
                    "instant": 0.05
                }
            }
        return {}
    
    async def discover_openai_models(self) -> Dict:
        """Discover OpenAI models"""
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key or api_key.startswith("your"):
            return {}
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Get available models
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    all_models = [m['id'] for m in data.get('data', [])]
                    
                    # Filter for chat models
                    chat_models = [
                        m for m in all_models 
                        if any(x in m for x in ['gpt-4', 'gpt-3.5', 'gpt-4o'])
                    ]
                    
                    # Sort by capability (best first)
                    priority_order = ['gpt-4o', 'gpt-4-turbo', 'gpt-4', 'gpt-3.5-turbo-16k', 'gpt-3.5-turbo']
                    chat_models.sort(key=lambda x: next((i for i, p in enumerate(priority_order) if p in x), 999))
                    
                    if chat_models:
                        return {
                            "provider": "openai",
                            "enabled": True,
                            "models": chat_models[:10],  # Top 10
                            "default": "gpt-3.5-turbo" if "gpt-3.5-turbo" in chat_models else chat_models[-1],
                            "api_key_var": "OPENAI_API_KEY",
                            "cost_factors": {
                                "gpt-4o": 0.8,
                                "gpt-4": 1.0,
                                "gpt-3.5": 0.1
                            }
                        }
        except Exception as e:
            logger.error(f"OpenAI discovery error: {e}")
        
        return {}
    
    async def discover_google_models(self) -> Dict:
        """Discover Google AI models"""
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if not api_key or api_key.startswith("your"):
            return {}
        
        models = ["gemini-pro", "gemini-pro-vision", "gemini-1.5-pro", "gemini-1.5-flash"]
        
        # Simple check if key works
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"https://generativelanguage.googleapis.com/v1/models?key={api_key}"
                )
                if response.status_code == 200:
                    return {
                        "provider": "google",
                        "enabled": True,
                        "models": models,
                        "default": "gemini-pro",
                        "api_key_var": "GOOGLE_API_KEY"
                    }
        except:
            pass
        
        return {}
    
    async def discover_ollama_models(self) -> Dict:
        """Discover local Ollama models"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get("http://zoe-ollama:11434/api/tags")
                
                if response.status_code == 200:
                    data = response.json()
                    models = [m['name'] for m in data.get('models', [])]
                    
                    if models:
                        return {
                            "provider": "ollama",
                            "enabled": True,
                            "models": models,
                            "default": "llama3.2:3b" if "llama3.2:3b" in models else models[0],
                            "api_key_var": None,
                            "cost_factors": {"all": 0}  # Free!
                        }
        except Exception as e:
            logger.warning(f"Ollama discovery failed: {e}")
        
        # Fallback - assume basic model exists
        return {
            "provider": "ollama",
            "enabled": True,
            "models": ["llama3.2:3b"],
            "default": "llama3.2:3b",
            "api_key_var": None,
            "cost_factors": {"all": 0}
        }
    
    async def discover_groq_models(self) -> Dict:
        """Discover Groq models"""
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            return {}
        
        models = ["llama2-70b-4096", "mixtral-8x7b-32768", "gemma-7b-it"]
        
        return {
            "provider": "groq",
            "enabled": True,
            "models": models,
            "default": models[0],
            "api_key_var": "GROQ_API_KEY",
            "cost_factors": {"all": 0.01}  # Very cheap
        }
    
    async def discover_together_models(self) -> Dict:
        """Discover Together AI models"""
        api_key = os.getenv("TOGETHER_API_KEY", "").strip()
        if not api_key:
            return {}
        
        models = [
            "mistralai/Mixtral-8x7B-Instruct-v0.1",
            "meta-llama/Llama-2-70b-chat-hf",
            "NousResearch/Nous-Hermes-2-Mixtral-8x7B-DPO"
        ]
        
        return {
            "provider": "together",
            "enabled": True,
            "models": models,
            "default": models[0],
            "api_key_var": "TOGETHER_API_KEY",
            "cost_factors": {"all": 0.02}
        }
    
    def get_default_routing_rules(self) -> Dict:
        """Get intelligent routing rules"""
        return {
            "complexity_analysis": {
                "simple": {
                    "indicators": ["hi", "hello", "thanks", "yes", "no", "ok"],
                    "max_words": 5,
                    "preferred_providers": ["ollama"]
                },
                "medium": {
                    "indicators": ["explain", "what is", "how does", "tell me about"],
                    "max_words": 30,
                    "preferred_providers": ["ollama", "openai", "anthropic"]
                },
                "complex": {
                    "indicators": ["implement", "create", "build", "design", "analyze", "optimize"],
                    "min_words": 20,
                    "preferred_providers": ["anthropic", "openai", "google"]
                },
                "expert": {
                    "indicators": ["architecture", "distributed", "microservices", "algorithm", "proof"],
                    "min_words": 30,
                    "preferred_providers": ["anthropic", "openai"]
                }
            },
            "cost_optimization": {
                "max_cost_per_query": 0.10,
                "daily_budget": 5.00,
                "prefer_local_under_words": 10
            }
        }
    
    def analyze_complexity(self, message: str, context: Dict = None) -> str:
        """Intelligent complexity analysis - not just word count!"""
        message_lower = message.lower()
        word_count = len(message.split())
        
        # Check for complexity indicators
        rules = self.config.get("routing_rules", {}).get("complexity_analysis", {})
        
        # Expert level checks
        expert_indicators = rules.get("expert", {}).get("indicators", [])
        if any(indicator in message_lower for indicator in expert_indicators):
            return "expert"
        
        # Complex checks
        complex_indicators = rules.get("complex", {}).get("indicators", [])
        if any(indicator in message_lower for indicator in complex_indicators):
            if word_count >= rules.get("complex", {}).get("min_words", 20):
                return "complex"
            return "medium"  # Complex topic but short query
        
        # Code detection
        if "```" in message or "def " in message or "function " in message:
            return "complex"
        
        # Simple checks
        simple_indicators = rules.get("simple", {}).get("indicators", [])
        if any(indicator in message_lower for indicator in simple_indicators):
            if word_count <= rules.get("simple", {}).get("max_words", 5):
                return "simple"
        
        # Word count based fallback
        if word_count <= 5:
            return "simple"
        elif word_count <= 30:
            return "medium"
        elif word_count <= 100:
            return "complex"
        else:
            return "expert"
    
    def get_model_for_request(self, complexity: str = None, message: str = None, context: Dict = None) -> Tuple[str, str]:
        """
        Intelligent model selection based on complexity and availability
        Now with REAL intelligence!
        """
        # If message provided, analyze it
        if message and not complexity:
            complexity = self.analyze_complexity(message, context)
        
        if not complexity:
            complexity = "medium"
        
        logger.info(f"🧠 Complexity analyzed as: {complexity}")
        
        # Get routing rules
        rules = self.config.get("routing_rules", {}).get("complexity_analysis", {})
        preferred_providers = rules.get(complexity, {}).get("preferred_providers", ["ollama"])
        
        # Try preferred providers in order
        for provider_name in preferred_providers:
            provider_config = self.config.get("providers", {}).get(provider_name, {})
            
            if provider_config.get("enabled"):
                models = provider_config.get("models", [])
                if models:
                    # Select model based on complexity within provider
                    if complexity == "simple" or complexity == "medium":
                        # Use cheaper/faster model
                        model = provider_config.get("default", models[-1])
                    else:
                        # Use more capable model
                        model = models[0] if len(models) > 1 else provider_config.get("default", models[0])
                    
                    logger.info(f"✅ Selected: {provider_name}/{model} for {complexity} query")
                    return provider_name, model
        
        # Fallback to Ollama
        ollama = self.config.get("providers", {}).get("ollama", {})
        model = ollama.get("default", "llama3.2:3b")
        logger.info(f"📦 Fallback to: ollama/{model}")
        return "ollama", model
    
    def get_fallback_config(self) -> Dict:
        """Emergency fallback configuration"""
        return {
            "providers": {
                "ollama": {
                    "enabled": True,
                    "models": ["llama3.2:3b"],
                    "default": "llama3.2:3b",
                    "api_key_var": None
                }
            },
            "discovered_at": datetime.now().isoformat(),
            "routing_rules": self.get_default_routing_rules()
        }
    
    def get_available_providers(self) -> List[str]:
        """Get list of enabled providers"""
        return [
            name for name, config in self.config.get("providers", {}).items()
            if config.get("enabled")
        ]
    
    def get_usage_stats(self) -> Dict:
        """Get usage statistics"""
        return {
            "total_requests": sum(self.usage_stats.values()),
            "by_provider": self.usage_stats,
            "by_complexity": {},
            "estimated_cost": 0
        }

# Create instance
if __name__ == "__main__":
    # Test discovery
    manager = LLMModelManager()
    print(f"Discovered providers: {manager.get_available_providers()}")
    
    # Test routing
    test_queries = [
        "Hi",
        "Explain REST API",
        "Build a microservices architecture with CQRS and event sourcing"
    ]
    
    for query in test_queries:
        complexity = manager.analyze_complexity(query)
        provider, model = manager.get_model_for_request(message=query)
        print(f'"{query[:30]}..." → {complexity} → {provider}/{model}')
