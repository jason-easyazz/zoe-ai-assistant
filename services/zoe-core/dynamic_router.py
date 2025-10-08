"""
Dynamic Router - Uses real discovered models
"""

import json
import os
from pathlib import Path
from typing import Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

class DynamicRouter:
    def __init__(self):
        self.models_file = Path("/app/data/llm_models.json")
        self.load_discovered_models()
        
    def load_discovered_models(self):
        """Load the dynamically discovered models"""
        if self.models_file.exists():
            with open(self.models_file) as f:
                data = json.load(f)
                self.providers = data.get("providers", {})
                self.default_provider = data.get("default_provider", "ollama")
        else:
            self.providers = {}
            self.default_provider = "ollama"
    
    def get_best_model_for_complexity(self, complexity: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Select best model based on complexity and what's actually available
        Uses REAL discovered models, not hardcoded lists
        """
        
        # Refresh discovered models
        self.load_discovered_models()
        
        # Define provider preference by complexity
        # But only use what's ACTUALLY available
        if complexity == "simple":
            # Prefer fast, cheap models
            provider_order = ["ollama", "groq", "cohere", "mistral", "openai", "google", "anthropic"]
            size_preference = "small"  # Prefer smaller models
        elif complexity == "medium":
            # Balance speed and quality
            provider_order = ["ollama", "mistral", "openai", "google", "anthropic", "groq"]
            size_preference = "medium"
        else:  # complex
            # Best quality
            provider_order = ["anthropic", "openai", "google", "mistral", "ollama", "groq"]
            size_preference = "large"
        
        # Try providers in preference order
        for provider_name in provider_order:
            provider = self.providers.get(provider_name, {})
            
            if not provider.get("enabled"):
                continue
                
            models = provider.get("models", [])
            if not models:
                continue
            
            # Select model based on size preference
            selected_model = self.select_model_by_size(models, size_preference, provider_name)
            
            if selected_model:
                logger.info(f"Selected {provider_name}/{selected_model} for {complexity} query")
                return provider_name, selected_model
        
        # Fallback to any available model
        for provider_name, provider in self.providers.items():
            if provider.get("enabled") and provider.get("models"):
                return provider_name, provider["models"][0]
        
        return None, None
    
    def select_model_by_size(self, models: list, size_pref: str, provider: str) -> Optional[str]:
        """
        Select model based on size preference
        Uses heuristics based on model names (since they're dynamically discovered)
        """
        if not models:
            return None
        
        # Size indicators in model names
        small_indicators = ["small", "tiny", "1b", "3b", "7b", "haiku", "turbo", "light", "mini"]
        medium_indicators = ["medium", "13b", "30b", "mixtral", "sonnet", "pro"]
        large_indicators = ["large", "70b", "opus", "ultra", "gpt-4", "claude-3"]
        
        if size_pref == "small":
            # Look for small models first
            for model in models:
                model_lower = model.lower()
                if any(ind in model_lower for ind in small_indicators):
                    return model
            # If no small model, take the last one (usually smaller)
            return models[-1] if models else None
            
        elif size_pref == "large":
            # Look for large models first
            for model in models:
                model_lower = model.lower()
                if any(ind in model_lower for ind in large_indicators):
                    return model
            # If no large model, take the first one (usually larger)
            return models[0] if models else None
            
        else:  # medium
            # Look for medium models
            for model in models:
                model_lower = model.lower()
                if any(ind in model_lower for ind in medium_indicators):
                    return model
            # Take middle model
            if len(models) > 1:
                return models[len(models) // 2]
            return models[0] if models else None
    
    def refresh_discovery(self):
        """Trigger a new discovery of models"""
        import subprocess
        result = subprocess.run(
            ["python3", "-c", "from llm_models import LLMModelManager; import asyncio; m = LLMModelManager(); asyncio.run(m.discover_all_models())"],
            capture_output=True,
            text=True,
            cwd="/app"
        )
        if result.returncode == 0:
            self.load_discovered_models()
            return True
        return False

# Global instance
dynamic_router = DynamicRouter()
