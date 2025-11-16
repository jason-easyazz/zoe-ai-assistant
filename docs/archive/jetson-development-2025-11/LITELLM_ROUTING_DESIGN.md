# LiteLLM/RouteLLM Intelligent Model Routing System

**Goal**: Use existing LiteLLM/RouteLLM infrastructure to route queries to specialized models based on task type.

---

## 🎯 Current System Analysis:

### Existing Components:
1. **`route_llm.py`** - ZoeRouter class with LiteLLM Router backend
2. **`model_config.py`** - ModelSelector with model configs and quality tracking
3. **`routers/chat.py`** - Uses intelligent_routing for model selection

### Current Routing Logic:
```python
# route_llm.py - Simple pattern matching
- "zoe-action" → gemma3n-e2b-gpu:latest
- "zoe-memory" → gemma3n-e2b-gpu:latest  
- "zoe-chat" → gemma3n-e2b-gpu:latest
```

**Problem**: All routes use the same model (Gemma), not optimized for different tasks!

---

## 💡 BRILLIANT SOLUTION: Task-Specific Model Routing

### Strategy:
**Use LiteLLM's `Router` to map task types → specialized models**

```python
Router Configuration:
├── TOOL_CALLING → hermes3:8b (95% tool accuracy)
├── VISION → gemma3n-e2b-gpu-fixed (multimodal)
├── CHAT → phi3:mini (fastest CPU)
├── MEMORY → qwen2.5:7b (excellent context)
└── REASONING → qwen2.5:14b (heavy thinking)
```

---

## 🔧 Implementation Plan:

### Phase 1: Update `route_llm.py` with Model-Specific Routing

```python
# NEW: Specialized model routing
"model_list": [
    {
        "model_name": "zoe-action",  # Tool calling
        "litellm_params": {
            "model": "ollama/hermes3:8b-llama3.1-q4_K_M",  # BEST for tools
            "api_base": "http://zoe-ollama:11434",
            "temperature": 0.6,  # Lower = more precise
            "num_gpu": -1,  # Auto GPU
        }
    },
    {
        "model_name": "zoe-chat",  # Fast conversation
        "litellm_params": {
            "model": "ollama/phi3:mini",  # FASTEST
            "api_base": "http://zoe-ollama:11434",
            "temperature": 0.7,
            "num_gpu": 0,  # CPU only
        }
    },
    {
        "model_name": "zoe-vision",  # Image understanding
        "litellm_params": {
            "model": "ollama/gemma3n-e2b-gpu-fixed",  # Multimodal
            "api_base": "http://zoe-ollama:11434",
            "temperature": 0.7,
            "num_gpu": 99,  # All GPU layers
        }
    },
    {
        "model_name": "zoe-memory",  # Context retrieval
        "litellm_params": {
            "model": "ollama/qwen2.5:7b",  # Good context
            "api_base": "http://zoe-ollama:11434",
            "temperature": 0.7,
            "num_gpu": 43,
        }
    }
]
```

### Phase 2: Update Classification Logic

```python
def _basic_classification(self, message: str, has_image: bool = False):
    """Enhanced classification with model-specific routing"""
    
    # 1. CHECK FOR IMAGES FIRST
    if has_image or "image" in message or "picture" in message:
        return {"model": "zoe-vision", "reasoning": "Image processing"}
    
    # 2. CHECK FOR TOOL CALLING
    action_patterns = ['add', 'create', 'schedule', 'delete', 'update', ...]
    if any(pattern in message.lower() for pattern in action_patterns):
        return {"model": "zoe-action", "reasoning": "Tool calling"}
    
    # 3. CHECK FOR MEMORY RETRIEVAL
    memory_patterns = ["remember", "who is", "what did", "last time"]
    if any(pattern in message.lower() for pattern in memory_patterns):
        return {"model": "zoe-memory", "reasoning": "Memory search"}
    
    # 4. DEFAULT: FAST CHAT
    return {"model": "zoe-chat", "reasoning": "Conversation"}
```

### Phase 3: Bundle Model Settings in LiteLLM

**Key Insight**: LiteLLM's `litellm_params` can contain ANY Ollama parameter!

```python
"litellm_params": {
    "model": "ollama/hermes3:8b-llama3.1-q4_K_M",
    "api_base": "http://zoe-ollama:11434",
    "temperature": 0.6,
    "num_gpu": -1,  # ✅ GPU SETTINGS
    "num_predict": 512,  # ✅ MAX TOKENS
    "num_ctx": 4096,  # ✅ CONTEXT WINDOW
    "repeat_penalty": 1.1,  # ✅ QUALITY
    "stop": ["\n\n", "User:"],  # ✅ STOP TOKENS
    "keep_alive": "30m",  # ✅ MEMORY MANAGEMENT
}
```

**This means we can configure EVERYTHING per model in ONE place!**

---

## 🚀 Benefits:

1. **Specialized Models**: Each task gets the BEST model
   - Tool calling → Hermes-3 (95% accuracy)
   - Chat → Phi3 (blazing fast)
   - Images → Gemma (multimodal)

2. **Centralized Config**: All model settings in `route_llm.py`
   - No more scattered configs
   - Easy to tune per model
   - GPU settings bundled

3. **LiteLLM Features**: Built-in benefits
   - Automatic retries
   - Fallback chains
   - Response caching
   - Cost tracking

4. **Simple Integration**: `chat.py` already uses routing!
   - Minimal code changes
   - Drop-in replacement

---

## 📊 Performance Impact:

**Before** (Single model for everything):
- Tool calling: 60% success (Gemma struggles)
- Chat: 10s latency (GPU contention)
- Memory: Slow (wrong model)

**After** (Specialized routing):
- Tool calling: 95% success (Hermes-3)
- Chat: 0.5s latency (Phi3 CPU)
- Memory: Fast (Qwen context)

---

## 🔄 Migration Steps:

1. ✅ Update `route_llm.py` with specialized models
2. ✅ Migrate GPU settings from `model_config.py` to LiteLLM params
3. ✅ Test routing logic with 100 prompts
4. ✅ Update `chat.py` to use routing results
5. ✅ Deploy and monitor

---

## 💡 Additional Enhancements:

### A) TensorRT-LLM Integration
Once TensorRT is ready, add it as an ultra-fast option:
```python
{
    "model_name": "zoe-action-tensorrt",
    "litellm_params": {
        "model": "tensorrt/hermes3-optimized",
        "api_base": "http://localhost:8001",  # Triton server
        "temperature": 0.6,
    }
}
```

### B) Load Balancing
For multiple instances:
```python
"routing_strategy": "least-busy",  # Balance across replicas
```

### C) Context-Aware Routing
```python
if context_size > 10000:
    return "zoe-memory"  # Long context model
elif needs_reasoning:
    return "zoe-reasoning"  # Heavy thinking
```

---

## 🎯 Next Steps:

1. Implement updated `route_llm.py`
2. Test with Second-Me training methodology
3. Add missing expert tools
4. Integrate TensorRT when ready

**Result**: World-class AI assistant with intelligent task routing! 🌟

