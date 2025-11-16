# Better Models for Tool Calling

## Current Problem
`gemma3n-e2b-gpu-fixed` (5.6GB) is NOT trained for function calling, despite aggressive prompting.

## Solutions

### Option 1: Use Pre-Trained Function-Calling Models (RECOMMENDED ⭐)

#### Best Models for Function Calling (Fit in 16GB):

1. **Hermes-3-Llama-3.1-8B** (4.7GB) ⭐⭐⭐
   - Specifically trained for function calling
   - High accuracy in structured JSON outputs
   - Works great with Ollama
   - Pull: `ollama pull adrienbrault/hermes-3-llama-3.1-8b:q4_K_M`
   - **Best choice for Jetson Orin NX**

2. **Qwen2.5-7B-Instruct** (4.7GB) ⭐⭐⭐
   - Excellent function calling support
   - Fast inference
   - Good reasoning capabilities
   - Pull: `ollama pull qwen2.5:7b-instruct-q4_K_M`
   - **Already in your fallback chain!**

3. **Nous-Hermes-2-Pro-Mistral-7B** (4.1GB) ⭐⭐
   - Fine-tuned for function calling
   - Very reliable for tool use
   - Pull: `ollama pull adrienbrault/nous-hermes-2-pro-mistral-7b:q4_K_M`

4. **NexusRaven-V2-13B** (7.3GB)
   - Best-in-class function calling
   - Outperforms GPT-4 in some benchmarks
   - Might be tight on 16GB but worth trying
   - Pull: `ollama pull nexusraven-v2-13b:q4_K_M` (if available)

#### Comparison:

| Model | Size | Speed | Function Calling | Fits 16GB |
|-------|------|-------|------------------|-----------|
| gemma3n-e2b-gpu-fixed | 5.6GB | Fast | ❌ No | ✅ Yes |
| Hermes-3-Llama-3.1-8B | 4.7GB | Fast | ✅ Excellent | ✅ Yes |
| Qwen2.5-7B-Instruct | 4.7GB | Very Fast | ✅ Excellent | ✅ Yes |
| Nous-Hermes-2-Pro | 4.1GB | Fast | ✅ Good | ✅ Yes |

---

### Option 2: Fine-Tune gemma3n (SLOWER)

#### Pros:
- Keep using familiar model
- Customize for your exact use cases
- Can train on your specific tool patterns

#### Cons:
- Requires training data (hundreds of examples)
- Takes time (hours to days)
- Needs GPU resources
- Complex setup

#### How to Fine-Tune:
1. Create training dataset:
```json
[
  {"input": "Add bread to shopping list", "output": "[TOOL_CALL:add_to_list:{\"list_name\":\"shopping\",\"task_text\":\"bread\"}]"},
  {"input": "Schedule dentist tomorrow 2pm", "output": "[TOOL_CALL:create_calendar_event:{\"title\":\"Dentist\",\"start_date\":\"tomorrow\",\"start_time\":\"14:00\"}]"}
]
```

2. Use unsloth or axolotl for efficient fine-tuning
3. Takes 100-500 examples for good results
4. Requires 8-16GB VRAM during training

---

### Option 3: Auto-Inject Tool Calls (CURRENT WORKAROUND ✅)

**Already implemented!** Auto-inject tool calls when LLM doesn't generate them.

**Pros:**
- Works immediately
- No model changes needed
- Reliable execution

**Cons:**
- Less flexible than true function calling
- Requires pattern matching for each action type
- Can't handle complex multi-step reasoning

---

## 🎯 RECOMMENDED ACTION

### Immediate (5 minutes):
**Switch to Hermes-3 or Qwen2.5** - both are MUCH better at function calling:

```bash
# Option A: Hermes-3 (recommended)
docker exec zoe-ollama ollama pull adrienbrault/hermes-3-llama-3.1-8b:q4_K_M

# Option B: Qwen2.5 (already have qwen2.5:7b, check if it has function calling)
docker exec zoe-ollama ollama pull qwen2.5:7b-instruct-q4_K_M
```

Then update `model_config.py`:
```python
self.current_model = "adrienbrault/hermes-3-llama-3.1-8b:q4_K_M"
# OR
self.current_model = "qwen2.5:7b-instruct-q4_K_M"
```

### Medium-term (if needed):
Fine-tune if specific customization required.

---

## 📊 Expected Results

### With Hermes-3 or Qwen2.5:
- ✅ Tool calls generated naturally
- ✅ No auto-injection needed
- ✅ Better accuracy
- ✅ Faster responses (smaller model)
- ✅ 100% test pass rate achievable

### Current (with auto-injection):
- ⚠️ Works but brittle
- ⚠️ Limited to patterns we code
- ⚠️ Not learning from examples

---

## 🚀 QUICK START

1. **Pull Hermes-3:**
```bash
docker exec zoe-ollama ollama pull adrienbrault/hermes-3-llama-3.1-8b:q4_K_M
```

2. **Update model config:**
```python
# /home/zoe/assistant/services/zoe-core/model_config.py
self.current_model = "adrienbrault/hermes-3-llama-3.1-8b:q4_K_M"
```

3. **Pre-warm it:**
```python
# /home/zoe/assistant/services/zoe-core/model_prewarm.py
models = ["adrienbrault/hermes-3-llama-3.1-8b:q4_K_M"]
```

4. **Test:**
```bash
curl -X POST "http://localhost:8000/api/chat?stream=false" \\
  -H "X-Session-ID: dev-localhost" \\
  -d '{"message": "Add apples to shopping list", "user_id": "test"}'
```

Expected: Tool call generated naturally, apples actually added!

---

## 💡 WHY THIS WORKS

**Hermes-3 and Qwen2.5** are:
- Trained on function calling datasets
- Understand JSON tool formats
- Follow system instructions better
- Designed for agentic workflows

**gemma3n** was designed for:
- General conversation
- Multimodal tasks (images)
- NOT function calling

**The right tool for the job makes all the difference!**

