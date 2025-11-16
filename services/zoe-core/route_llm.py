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
                        "model_name": "zoe-action",  # Tool calling - BEST MODEL
                        "litellm_params": {
                            "model": "vllm/qwen2.5-coder-7b",  # 98% tool accuracy with AWQ
                            "api_base": os.getenv("VLLM_BASE", "http://zoe-vllm:11434"),
                            "temperature": 0.6,  # Lower = more precise
                            "num_gpu": -1,  # Auto GPU allocation
                            "num_predict": 512,
                            "num_ctx": 4096,
                            "repeat_penalty": 1.1,
                            "stop": ["\n\n", "User:", "Human:"],
                            "keep_alive": "30m",
                        },
                        "model_info": {"id": "tool-calling-specialist"},
                    },
                    {
                        "model_name": "zoe-chat",  # Fast conversation
                        "litellm_params": {
                            "model": "vllm/llama-3.2-3b",  # Fast conversation with AWQ
                            "api_base": os.getenv("VLLM_BASE", "http://zoe-vllm:11434"),
                            "temperature": 0.7,
                            "num_gpu": 0,  # CPU only for speed
                            "num_predict": 256,
                            "num_ctx": 2048,
                            "repeat_penalty": 1.1,
                            "stop": ["\n\n", "User:", "Human:"],
                            "keep_alive": "30m",
                        },
                        "model_info": {"id": "fast-chat-specialist"},
                    },
                    {
                        "model_name": "zoe-vision",  # Image/multimodal
                        "litellm_params": {
                            "model": "vllm/qwen2-vl-7b",  # Vision with AWQ
                            "api_base": os.getenv("VLLM_BASE", "http://zoe-vllm:11434"),
                            "temperature": 0.7,
                            "num_gpu": 99,  # All GPU layers
                            "num_predict": 256,
                            "num_ctx": 2048,
                            "repeat_penalty": 1.1,
                            "stop": ["\n\n", "User:", "Human:"],
                            "keep_alive": "30m",
                        },
                        "model_info": {"id": "vision-specialist"},
                    },
                    {
                        "model_name": "zoe-memory",  # Context-heavy retrieval
                        "litellm_params": {
                            "model": "vllm/qwen2.5-coder-7b",  # Excellent context handling with AWQ
                            "api_base": os.getenv("VLLM_BASE", "http://zoe-vllm:11434"),
                            "temperature": 0.7,
                            "num_gpu": 43,  # Qwen-optimized layers
                            "num_predict": 512,
                            "num_ctx": 4096,
                            "repeat_penalty": 1.1,
                            "stop": ["\n\n", "User:", "Human:"],
                            "keep_alive": "30m",
                        },
                        "model_info": {"id": "memory-retrieval-specialist"},
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
        
        # Action patterns (comprehensive)
        action_patterns = [
            'add to', 'add ', 'create ', 'schedule ', 'remind ', 'set ', 'turn on', 'turn off',
            'list ', 'show ', 'get ', 'find ', 'search ', 'delete ', 'remove ', 'update ',
            "don't let me forget", "i need to buy", "put on my list", "buy some", "get some",
            'shopping list', 'shopping', 'grocery list', 'groceries',
            'todo list', 'calendar', 'event', 'task', 'appointment', 'meeting',
        ]
        
        # Memory patterns
        memory_patterns = ["remember", "memory", "recall", "what did", "last time", "who is", "when did"]
        
        # Classify
        is_action = any(pattern in msg for pattern in action_patterns)
        requires_memory = any(k in msg for k in memory_patterns)
        
        if is_action:
            model = "zoe-action"
            reasoning = "Action detected - tool calling required"
        elif requires_memory:
            model = "zoe-memory"
            reasoning = "Memory retrieval detected"
        else:
            model = "zoe-chat"
            reasoning = "General conversation"
        
        return {
            "model": model,
            "confidence": 0.85 if is_action or requires_memory else 0.7,
            "reasoning": reasoning,
            "requires_memory": requires_memory,
            "complexity": "standard",
        }

    def classify_query(self, message: str, context: Dict, has_image: bool = False) -> Dict:
        """Classify query and route to appropriate specialized model"""
        decision = self._basic_classification(message)
        return decision

    async def route_query(self, message: str, context: Dict, routing_model: str = None, has_image: bool = False) -> Dict:
        """Async route query to specialized model
        
        Args:
            message: User message
            context: Conversation context
            routing_model: (Unused) For future routing decisions
            has_image: Whether query includes image
        
        Returns:
            Dict with model selection and reasoning
        """
        return self.classify_query(message, context, has_image)


# Global instance for imports like `from route_llm import router`
router = ZoeRouter()
