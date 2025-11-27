# System Architecture Audit Results
**Date**: 2025-11-22  
**Status**: 🔧 **Issues Found & Being Fixed**

## Executive Summary

Audit found **critical architecture inconsistencies** preventing proper system operation. All systems are running, but model routing and configuration mismatches are causing failures.

---

## 🚨 Critical Issues Found

### 1. **LiteLLM Config Mismatch** (FIXED ✅)
**Problem**: 
- LiteLLM service config (`services/zoe-litellm/minimal_config.yaml`) was configured for `smollm2-1.7b`
- Actual loaded model in `zoe-llamacpp` is `llama3.2-3b` (per `docker-compose.yml`)
- All model aliases pointed to wrong model path

**Fix Applied**:
- Updated all model entries in `minimal_config.yaml` to use `llama3.2-3b` path:
  - Model path: `openai//models/llama-3.2-3b-gguf/Llama-3.2-3B-Instruct-Q4_K_M.gguf`
  - All aliases (`qwen2.5:7b`, `phi3:mini`, `hermes3-8b`, etc.) now point to correct model
  - Updated fallback chains to match

**Status**: ✅ Config updated, requires LiteLLM service restart to take effect

---

### 2. **Model Selector Returns Wrong Names** (FIXED ✅)
**Problem**:
- `model_selector.select_model()` returns model names like `llama3.2:3b`
- These names don't match LiteLLM service model aliases (`qwen2.5:7b`, `phi3:mini`, etc.)
- Chat interface fails with "Invalid model name" errors

**Fix Applied**:
- Updated `model_config.py` fallback chains to use LiteLLM service model names:
  - Jetson: `qwen2.5:7b` → `phi3:mini` → `hermes3-8b` → `local-model`
  - Pi5: `phi3:mini` → `local-fast` → `local-model` → `smollm2-1.7b`
- Updated `_get_best_conversation_model()` to return `qwen2.5:7b` or `phi3:mini`
- Updated `_get_best_action_model()` to use LiteLLM service model names

**Status**: ✅ Code updated, requires service restart

---

### 3. **route_llm.py Architecture** (VERIFIED ✅)
**Status**: ✅ **Correct** - `route_llm.py` creates internal LiteLLM Router for classification decisions
- Connects directly to `zoe-llamacpp:11434` for routing decisions
- Returns model names like "zoe-action", "zoe-chat", "zoe-memory"
- These are then mapped by `model_selector` to actual LiteLLM service model names
- **No changes needed**

---

### 4. **chat.py LLM Service Usage** (VERIFIED ✅)
**Status**: ✅ **Correct** - `chat.py` uses LiteLLM service for actual LLM calls
- Uses `http://zoe-litellm:8001/v1/chat/completions`
- Passes model names from `model_selector` (which now match LiteLLM service aliases)
- **No changes needed**

---

## 📊 System Architecture Flow (After Fixes)

```
User Request
    ↓
chat.py (routers/chat.py)
    ↓
intelligent_routing()
    ├─ route_llm.py (classification)
    │  └─ Returns: "zoe-action", "zoe-chat", "zoe-memory"
    │
    └─ model_selector.select_model()
       └─ Returns: "qwen2.5:7b", "phi3:mini", "local-fast" (LiteLLM service names)
    ↓
LLM Call via LiteLLM Service
    ├─ URL: http://zoe-litellm:8001/v1/chat/completions
    ├─ Model: "qwen2.5:7b" (or other LiteLLM alias)
    └─ LiteLLM Service
       └─ Routes to zoe-llamacpp:11434
          └─ Model: llama3.2-3b (actual loaded model)
```

---

## ✅ Fixes Summary

| Component | Issue | Fix | Status |
|-----------|-------|-----|--------|
| `services/zoe-litellm/minimal_config.yaml` | Wrong model paths | Updated all models to `llama3.2-3b` path | ✅ Fixed |
| `services/zoe-core/model_config.py` | Wrong model names | Updated to use LiteLLM service aliases | ✅ Fixed |
| `services/zoe-core/route_llm.py` | None found | Verified correct architecture | ✅ Verified |
| `services/zoe-core/routers/chat.py` | None found | Verified correct architecture | ✅ Verified |

---

## 🔄 Required Actions

1. **Restart LiteLLM Service** (to load new config):
   ```bash
   docker compose restart zoe-litellm
   ```

2. **Restart Zoe Core** (to use updated model selector):
   ```bash
   docker compose restart zoe-core
   ```

3. **Verify Models Available**:
   ```bash
   curl -H "Authorization: Bearer sk-f3320300bb32df8f176495bb888ba7c8f87a0d01c2371b50f767b9ead154175f" \
     http://localhost:8001/v1/models
   ```

4. **Test Chat Interface**:
   ```bash
   curl -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "Hello", "user_id": "test_user"}'
   ```

---

## 🎯 Expected Behavior After Fixes

1. **Model Selection**: `model_selector` returns names matching LiteLLM service (`qwen2.5:7b`, `phi3:mini`, etc.)
2. **LiteLLM Routing**: All model aliases route to `llama3.2-3b` via `zoe-llamacpp:11434`
3. **Chat Interface**: Works without "Invalid model name" errors
4. **Response Times**: Maintains <2s for voice-optimized responses

---

## 📝 Notes

- **Dual Routing System**: 
  - `route_llm.py` = Classification router (connects to `zoe-llamacpp` directly)
  - `chat.py` = Execution router (uses LiteLLM service)
  - This is **correct architecture** - no changes needed

- **Model Name Mapping**:
  - `route_llm.py` returns: "zoe-action", "zoe-chat", "zoe-memory"
  - `model_selector` maps to: "qwen2.5:7b", "phi3:mini", "local-fast"
  - LiteLLM service maps to: `llama3.2-3b` (actual model)

- **All Services Running**: ✅ All Docker containers are healthy

---

## 🚧 Remaining Issues (If Any)

After restarting services, monitor:
1. LiteLLM service logs for model loading errors
2. Chat interface for "Invalid model name" errors
3. Response times for performance regression

---

**Next Steps**: ✅ **COMPLETED** - Services restarted and verified working.

---

## ✅ Final Status

**All Systems Operational**: 
- ✅ Chat interface working (Status: 200)
- ✅ LiteLLM service healthy
- ✅ All Docker containers running
- ✅ Model routing configured correctly

**Architecture Verified**:
- ✅ Dual routing system (classification + execution) working correctly
- ✅ Model name mapping consistent across all systems
- ✅ LiteLLM config matches actual loaded model

**System Ready**: All components functional and following intended design.

