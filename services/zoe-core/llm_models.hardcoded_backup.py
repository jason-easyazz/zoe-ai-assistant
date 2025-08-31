"""Fixed LLM Model Manager"""
import os
import json

class LLMModelManager:
    def __init__(self):
        self.models_file = "/app/data/llm_models.json"
        
    def get_model_for_request(self, complexity="medium"):
        """Route to the RIGHT model"""
        # Developer/high complexity -> Anthropic Claude Opus
        if complexity == "high" and os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic", "claude-3-haiku-20240307"
        
        # Medium -> Try OpenAI but with REAL model
        if os.getenv("OPENAI_API_KEY"):
            return "openai", "gpt-3.5-turbo"  # Use a model that actually exists
            
        # Fallback to local
        return "ollama", "llama3.2:3b"
    
    async def discover_all_models(self):
        pass  # Not needed for now
        
    async def discover_ollama_models(self):
        return ["llama3.2:3b"]
