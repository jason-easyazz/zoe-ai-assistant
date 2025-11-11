# How to Switch Between Models

## Quick Switch Guide

Want to try Gemma for vision? Or Qwen for better accuracy? Here's how:

### Step 1: Choose Your Model

**Available Models:**
- `hermes3:8b-llama3.1-q4_K_M` - BEST for function calling (95% accuracy) - **CURRENT**
- `gemma3n-e2b-gpu-fixed` - BEST for vision/multimodal (needs auto-inject for tools)
- `qwen2.5:7b` - BEST balanced (90% accuracy, 4.7GB)
- `phi3:mini` - BEST for speed (2.2GB, lower accuracy)

### Step 2: Edit model_config.py

```python
# Line 274 in /home/zoe/assistant/services/zoe-core/model_config.py
self.current_model = "YOUR_MODEL_NAME_HERE"
```

**Example - Switch to Gemma:**
```python
self.current_model = "gemma3n-e2b-gpu-fixed"
```

### Step 3: Edit model_prewarm.py (Optional but Recommended)

```python
# Line 18 in /home/zoe/assistant/services/zoe-core/model_prewarm.py
models = ["YOUR_MODEL_NAME_HERE"]
```

**Example - Switch to Gemma:**
```python
models = ["gemma3n-e2b-gpu-fixed"]
```

### Step 4: Restart

```bash
docker restart zoe-ollama  # Unload old model
docker restart zoe-core    # Load new model
sleep 40                    # Wait for startup
```

### Step 5: Test

```bash
curl -X POST "http://localhost:8000/api/chat?stream=false" \
  -H "X-Session-ID: dev-localhost" \
  -H "Content-Type: application/json" \
  -d '{"message": "add test item to shopping list", "user_id": "test"}'
```

Look for: `"Executed add_to_list successfully"`

## What Changes Automatically

When you switch models, the system automatically:

1. **Uses Model-Specific Prompts**
   - Hermes-3: Concise, structured
   - Gemma: Heavy examples, pattern matching
   - Qwen: Clear function list
   - Phi/Llama: Simple rules

2. **Applies Optimal GPU Settings**
   - Hermes-3: `num_gpu=-1` (auto-detect)
   - Gemma: `num_gpu=99` (all layers)
   - Qwen: `num_gpu=43` (explicit)
   - Phi: `num_gpu=0` (CPU) or `num_gpu=1` (GPU)

3. **Adjusts Fallback Behavior**
   - Hermes-3: Rarely needs auto-inject
   - Gemma: Frequently uses auto-inject
   - Qwen: Occasionally needs auto-inject

## Memory Considerations (16GB Jetson)

**Can Load at Once:**
- ✅ Hermes-3 (4.9GB) **OR** Gemma (5.6GB)
- ✅ Qwen (4.7GB) **OR** Gemma (5.6GB)
- ✅ Phi (2.2GB) + Qwen (4.7GB) = 6.9GB ✅

**Cannot Load Together:**
- ❌ Hermes (4.9GB) + Gemma (5.6GB) = 10.5GB + overhead = OOM
- ❌ Qwen (4.7GB) + Gemma (5.6GB) = 10.3GB + overhead = OOM

**Solution**: Keep ONE primary model loaded with `keep_alive="30m"`

## Testing Different Models

### Quick Comparison Test

```bash
# Test Hermes-3
curl -X POST ... -d '{"message": "add bread", ...}'
# Note response time and tool call quality

# Switch to Gemma (edit config, restart)
curl -X POST ... -d '{"message": "add bread", ...}'
# Compare response time and tool call quality

# Switch to Qwen (edit config, restart)
curl -X POST ... -d '{"message": "add bread", ...}'
# Compare all three!
```

### What to Look For

1. **Tool Call Generation**: Does it generate `[TOOL_CALL:...]` natively?
2. **Response Time**: How long from request to completion?
3. **Natural Language**: Does the response sound natural and friendly?
4. **Success Rate**: Do actions actually execute?

## Recommendations by Use Case

### You Want: Best Tool Calling
**Choose**: Hermes-3 (current)
- 95% native tool call generation
- Fast enough (12s avg)
- Reliable

### You Want: Vision/Image Understanding
**Choose**: Gemma 3n E2B GPU Fixed
- Multimodal support
- Can analyze images
- Tools work via auto-inject

### You Want: Best Balance
**Choose**: Qwen 2.5 7B
- 90% native tool calling
- Similar size to Hermes
- Great alternative

### You Want: Maximum Speed
**Choose**: Phi3:mini
- Smallest model (2.2GB)
- Fastest responses
- Lower accuracy (40%)

## Troubleshooting

### Model Won't Load
```bash
# Check available memory
docker exec zoe-ollama ollama ps

# If other model still loaded, restart ollama
docker restart zoe-ollama
```

### Tool Calls Not Working
```bash
# Check logs for auto-injection
docker logs zoe-core --tail 50 | grep -E "TOOL_CALL|Auto-inject"

# If model doesn't support function calling well, auto-inject will save the day!
```

### OOM Errors
```bash
# Reduce num_gpu in model_config.py
# Example: Change from num_gpu=99 to num_gpu=1
```

## Need Help?

1. Check logs: `docker logs zoe-core --tail 100`
2. Verify model loaded: `docker exec zoe-ollama ollama ps`
3. Test simple query: `curl ... -d '{"message": "hello"}'`
4. Review: `MODEL_SPECIFIC_GPU_SETTINGS.md`

---

**Remember**: The beauty of this system is that you can experiment! Try different models, find what works best for your use case, and the system adapts automatically! 🚀
