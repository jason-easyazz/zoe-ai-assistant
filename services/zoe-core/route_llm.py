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
                            "model": "ollama/llama3.2:3b",
                            "api_base": os.getenv("OLLAMA_BASE", "http://localhost:11434"),
                            "temperature": 0.7,
                        },
                        "model_info": {"id": "zoe-memory-retrieval"},
                    },
                    {
                        "model_name": "zoe-chat",
                        "litellm_params": {
                            "model": "ollama/llama3.2:3b",
                            "api_base": os.getenv("OLLAMA_BASE", "http://localhost:11434"),
                            "temperature": 0.8,
                        },
                    },
                ],
                redis_host=os.getenv("ZOE_REDIS_HOST", "zoe-redis"),
                redis_port=int(os.getenv("ZOE_REDIS_PORT", "6379")),
                routing_strategy="simple-shuffle",
                num_retries=2,
                timeout=30,
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
        # Fallback to basic classification; LiteLLM Router does routing at call time
        return self._basic_classification(message)

    async def route_query(self, message: str, context: Dict) -> Dict:
        # If advanced classification API becomes available, use it; otherwise basic
        return self._basic_classification(message)


# Global instance for imports like `from route_llm import router`
router = ZoeRouter()
