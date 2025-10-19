"""LiteLLM-backed Router for Zoe using RouteLLM-style interface"""
import os
from typing import Dict, Any

try:
    from litellm import Router as LiteRouter
except Exception:  # litellm not installed yet
    LiteRouter = None


class ZoeRouter:
    """Router that leverages LiteLLM's Router for caching/fallbacks.
    Provides both async route_query and sync classify_query for compatibility.
    """

    def __init__(self):
        self.enabled = LiteRouter is not None
        self.router = None
        if self.enabled:
            # Minimal local-first config; can be overridden by env/config file
            self.router = LiteRouter(
                model_list=[
                    {
                        "model_name": "zoe-memory",
                        "litellm_params": {
                            "model": "ollama/gemma3:1b",  # Fastest model
                            "api_base": os.getenv("OLLAMA_BASE", "http://zoe-ollama:11434"),
                            "temperature": 0.7,
                            "max_tokens": 128,  # Speed optimization
                        },
                        "model_info": {"id": "zoe-memory-retrieval"},
                    },
                    {
                        "model_name": "zoe-chat",
                        "litellm_params": {
                            "model": "ollama/gemma3:1b",  # Fastest model
                            "api_base": os.getenv("OLLAMA_BASE", "http://zoe-ollama:11434"),
                            "temperature": 0.8,
                            "max_tokens": 128,  # Speed optimization
                        },
                    },
                    {
                        "model_name": "zoe-fast",
                        "litellm_params": {
                            "model": "ollama/qwen2.5:1.5b",  # Fast alternative
                            "api_base": os.getenv("OLLAMA_BASE", "http://zoe-ollama:11434"),
                            "temperature": 0.7,
                            "max_tokens": 128,
                        },
                    },
                ],
                redis_host=os.getenv("ZOE_REDIS_HOST", "zoe-redis"),
                redis_port=int(os.getenv("ZOE_REDIS_PORT", "6379")),
                routing_strategy="simple-shuffle",
                num_retries=2,
                timeout=20,  # Reduced timeout
                cache_responses=True,
            )

    def _basic_classification(self, message: str) -> Dict[str, Any]:
        msg = message.lower()
        requires_memory = any(k in msg for k in ["remember", "memory", "recall", "what did", "last time"])
        model = "zoe-memory" if requires_memory else "zoe-chat"
        return {
            "model": model,
            "confidence": 0.8,
            "reasoning": "Heuristic classification based on memory-related keywords",
            "requires_memory": requires_memory,
            "complexity": "standard",
        }

    def classify_query(self, message: str, context: Dict) -> Dict:
        # Context-aware tweaks: consider conversation history length and user_data size
        decision = self._basic_classification(message)
        history = context.get("conversation_history", [])
        context_size = len(history)
        user_data = context.get("user_data", {})
        # If large context/data present, bias toward memory model
        if context_size >= 6 or any(user_data.get(k) for k in ["calendar_events","recent_journal","people","projects","memories"]):
            decision["requires_memory"] = True
            decision["model"] = "zoe-memory"
            decision["reasoning"] += "; context-aware bias"
        return decision

    async def route_query(self, message: str, context: Dict) -> Dict:
        # Use same classification logic for async path
        return self.classify_query(message, context)


# Global instance for imports like `from route_llm import router`
router = ZoeRouter()
