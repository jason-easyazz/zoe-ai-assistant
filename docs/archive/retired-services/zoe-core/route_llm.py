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
            # Set dummy API key if not provided (required by LiteLLM even for custom api_base)
            if not os.getenv("OPENAI_API_KEY"):
                os.environ["OPENAI_API_KEY"] = "sk-dummy-key-for-local-llm"
            
            # ✅ UPDATED 2025-11-18: Using TESTED OPTIMIZED models (see MODEL_TEST_ANALYSIS.md)
            # NOW using llama.cpp server (zoe-llamacpp) with tested winning models
            self.router = LiteRouter(
                model_list=[
                    {
                        "model_name": "zoe-action",  # Tool calling - connects to llama.cpp directly for classification
                        "litellm_params": {
                            "model": "openai//models/llama-3.2-3b-gguf/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
                            "api_base": os.getenv("LLAMACPP_BASE", "http://zoe-llamacpp:11434/v1"),
                            "api_key": "sk-dummy",
                            "temperature": 0.7,
                            "num_predict": 256,
                            "num_ctx": 2048,
                            "repeat_penalty": 1.1,
                            "stop": ["\n\n", "User:", "Human:", "<tool_call>"],
                            "keep_alive": "30m",
                        },
                        "model_info": {"id": "tool-calling-specialist", "tested": True, "score": 75},
                    },
                    {
                        "model_name": "zoe-chat",  # Fast conversation
                        "litellm_params": {
                            "model": "openai//models/llama-3.2-3b-gguf/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
                            "api_base": os.getenv("LLAMACPP_BASE", "http://zoe-llamacpp:11434/v1"),
                            "api_key": "sk-dummy",
                            "temperature": 0.7,
                            "num_predict": 128,  # 🎙️ VOICE: Optimized for real-time
                            "num_ctx": 2048,
                            "repeat_penalty": 1.1,
                            "stop": ["\n\n", "User:", "Human:", "<tool_call>"],
                            "keep_alive": "30m",
                        },
                        "model_info": {"id": "fast-chat-specialist", "tested": True, "score": 75},
                    },
                    {
                        "model_name": "zoe-vision",  # Image/multimodal (FUTURE)
                        "litellm_params": {
                            "model": "openai//models/llama-3.2-3b-gguf/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
                            "api_base": os.getenv("LLAMACPP_BASE", "http://zoe-llamacpp:11434/v1"),
                            "api_key": "sk-dummy",
                            "temperature": 0.7,
                            "num_predict": 512,
                            "num_ctx": 2048,
                            "repeat_penalty": 1.1,
                            "stop": ["\n\n", "User:", "Human:"],
                            "keep_alive": "30m",
                        },
                        "model_info": {"id": "vision-specialist", "tested": False, "available": False},
                    },
                    {
                        "model_name": "zoe-memory",  # Context-heavy retrieval
                        "litellm_params": {
                            "model": "openai//models/llama-3.2-3b-gguf/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
                            "api_base": os.getenv("LLAMACPP_BASE", "http://zoe-llamacpp:11434/v1"),
                            "api_key": "sk-dummy",
                            "temperature": 0.7,
                            "num_predict": 256,
                            "num_ctx": 2048,
                            "repeat_penalty": 1.1,
                            "stop": ["\n\n", "User:", "Human:", "<tool_call>"],
                            "keep_alive": "30m",
                        },
                        "model_info": {"id": "memory-retrieval-specialist", "tested": True},
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
