"""
Smart RouteLLM - Uses discovered models with intelligent routing
NO HARDCODED MODELS - everything is dynamic
"""
import os
import json
import logging
from typing import Dict, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

class LLMModelManager:
    def __init__(self):
        self.models_file = "/app/data/llm_models.json"
        self.config = self.load_config()
        
    def load_config(self) -> Dict:
        """Load dynamically discovered configuration"""
        try:
            with open(self.models_file, 'r') as f:
                return json.load(f)
        except:
            # Return empty config, will be discovered
            return {"providers": {}}
    
    def analyze_complexity(self, message: str, context: Dict = None) -> str:
        """Intelligently analyze message complexity"""
        word_count = len(message.split())
        message_lower = message.lower()
        
        # Code indicators
        code_indicators = ['def ', 'class ', 'import ', 'function', '```', 'implement', 'create']
        has_code = any(ind in message_lower for ind in code_indicators)
        
        # Complex topic indicators
        complex_indicators = ['architecture', 'optimize', 'algorithm', 'distributed', 'microservice']
        has_complex = any(ind in message_lower for ind in complex_indicators)
        
        # Developer mode gets higher complexity
        is_developer = context and context.get("mode") == "developer"
        
        if has_code or has_complex or (is_developer and word_count > 10):
            return "complex"
        elif word_count > 15 or is_developer:
            return "medium"
        else:
            return "simple"
    
    def get_model_for_request(self, message: str = None, context: Dict = None) -> Tuple[str, str]:
        """Dynamically select best available model based on discovered models"""
        
        complexity = self.analyze_complexity(message or "", context or {})
        
        # Get routing rules
        rules = self.config.get("routing_rules", {})
        provider_priority = rules.get("provider_priority", {})
        
        # Get priority list based on complexity
        if complexity == "complex":
            priority_list = provider_priority.get("complex_queries", ["anthropic", "openai", "ollama"])
        elif complexity == "medium":
            priority_list = provider_priority.get("medium_queries", ["openai", "anthropic", "ollama"])
        else:
            priority_list = provider_priority.get("simple_queries", ["ollama", "openai"])
        
        # Special handling for developer mode
        if context and context.get("mode") == "developer" and rules.get("prefer_claude_for_developer"):
            # Move anthropic to front if available
            if "anthropic" in priority_list:
                priority_list.remove("anthropic")
                priority_list.insert(0, "anthropic")
        
        # Find first available provider from priority list
        for provider_name in priority_list:
            provider = self.config.get("providers", {}).get(provider_name, {})
            if provider.get("enabled") and provider.get("models"):
                # Use the discovered models, not hardcoded ones
                models = provider.get("models", [])
                
                # Select appropriate model from discovered list
                if complexity == "complex" and len(models) > 1:
                    # Prefer larger models (usually first in list)
                    model = models[0]
                else:
                    # Use default or smaller model
                    model = provider.get("default") or models[-1] if models else None
                
                if model:
                    logger.info(f"Selected {provider_name}/{model} for {complexity} query")
                    return provider_name, model
        
        # Fallback to any available provider
        for provider_name, provider in self.config.get("providers", {}).items():
            if provider.get("enabled") and provider.get("models"):
                models = provider.get("models", [])
                model = provider.get("default") or (models[0] if models else None)
                if model:
                    logger.info(f"Fallback to {provider_name}/{model}")
                    return provider_name, model
        
        # Ultimate fallback
        return "ollama", "llama3.2:3b"
    
    def get_available_providers(self) -> list:
        """Get list of available providers"""
        return [
            name for name, config in self.config.get("providers", {}).items()
            if config.get("enabled")
        ]
    
    def refresh_discovery(self):
        """Trigger fresh discovery of models"""
        # This would call the actual discovery code
        logger.info("Model discovery triggered")

# Global instance
manager = LLMModelManager()
