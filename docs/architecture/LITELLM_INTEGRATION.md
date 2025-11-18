# LiteLLM Gateway Integration - Architecture Document

**Status:** ✅ Production (Implemented 2025-11-17)  
**Owner:** System Architecture  
**Last Updated:** 2025-11-17

## 🎯 Executive Summary

LiteLLM Gateway is Zoe's **PRIMARY** LLM inference layer, providing a unified OpenAI-compatible API for all models (local + cloud). This architectural decision ensures:

- **Unified API**: Single endpoint for all models
- **Zero-code model switching**: Change models via config, not code
- **Built-in reliability**: Automatic fallbacks, retries, caching
- **Cost optimization**: Redis-backed caching (10min TTL)
- **Observability**: Centralized usage tracking

## 📐 Architecture Overview

```
┌─────────────────┐
│   User Request  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   zoe-core      │  RouteLLM: Routing Logic
│   /api/chat     │  (Decides WHICH model)
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────┐
│         LiteLLM Gateway (zoe-litellm:8001)          │
│  Execution Layer: HOW to call models                │
│                                                       │
│  ✓ OpenAI-compatible API                            │
│  ✓ Automatic fallbacks                              │
│  ✓ Redis caching (10min TTL)                        │
│  ✓ Load balancing (4 workers)                       │
│  ✓ Usage tracking                                   │
└─────┬──────────────┬────────────────┬───────────────┘
      │              │                │
      ▼              ▼                ▼
┌──────────┐  ┌──────────┐    ┌──────────┐
│zoe-      │  │ OpenAI   │    │Anthropic │
│llamacpp  │  │ API      │    │ API      │
│(local)   │  │ (cloud)  │    │ (cloud)  │
└──────────┘  └──────────┘    └──────────┘
```

## 🔑 Key Components

### 1. LiteLLM Gateway Service

**Container:** `zoe-litellm`  
**Port:** 8001  
**Endpoint:** `http://zoe-litellm:8001/v1/chat/completions`  
**Config:** `/home/zoe/assistant/services/zoe-litellm/minimal_config.yaml`

**Capabilities:**
- OpenAI-compatible API (drop-in replacement)
- Model routing and load balancing
- Redis-backed response caching
- Automatic retries and fallbacks
- Usage tracking and monitoring

### 2. Configuration File

**Location:** `services/zoe-litellm/minimal_config.yaml`

**Mounted as Volume:** ✅ Yes (read-only)
- Allows config updates without rebuilding
- Restart service to reload: `docker restart zoe-litellm`

**Key Sections:**

```yaml
model_list:
  - model_name: local-model          # Primary local model
  - model_name: smollm2-1.7b         # Specific model name
  - model_name: local-fast           # Fast variant (fewer tokens)
  - model_name: gpt-4o-mini          # Cloud fallback (OpenAI)
  - model_name: claude-3-5-sonnet    # Cloud fallback (Anthropic)

router_settings:
  model_group_alias:                 # Logical groupings
    fast-model: [local-fast, ...]
    function-calling: [local-model, gpt-4o-mini]
    powerful: [claude-3-5-sonnet, gpt-4o-mini]

general_settings:
  master_key: "sk-..."               # API authentication
  cache: true                        # Enable Redis caching
  cache_params:
    host: "zoe-redis"
    ttl: 600                         # 10 minutes
```

### 3. Integration Points

**Primary Integration:** `services/zoe-core/routers/chat.py`

```python
# Streaming endpoint (line ~755)
llm_url = "http://zoe-litellm:8001/v1/chat/completions"

# Provider abstraction (llm_provider.py)
provider = LiteLLMProvider()  # Default provider
```

**Authentication:**
- All requests require `Authorization: Bearer <master_key>` header
- Master key configured in `minimal_config.yaml`

## 🚀 Usage Examples

### Basic Chat Completion

```bash
curl -X POST http://zoe-litellm:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-f3320300bb32df8f176495bb888ba7c8f87a0d01c2371b50f767b9ead154175f" \
  -d '{
    "model": "local-model",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false,
    "max_tokens": 100
  }'
```

### Streaming Response

```python
import httpx
import json

async with httpx.AsyncClient() as client:
    async with client.stream(
        "POST",
        "http://zoe-litellm:8001/v1/chat/completions",
        headers={"Authorization": "Bearer <master_key>"},
        json={
            "model": "local-model",
            "messages": [{"role": "user", "content": "Tell me a story"}],
            "stream": True
        }
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if "choices" in data:
                    content = data["choices"][0].get("delta", {}).get("content", "")
                    print(content, end="", flush=True)
```

### List Available Models

```bash
curl http://zoe-litellm:8001/v1/models \
  -H "Authorization: Bearer <master_key>"
```

## 🔄 Model Management

### Current Local Model

LiteLLM proxies to `zoe-llamacpp`, which loads **ONE model at a time**.

**Check currently loaded model:**
```bash
docker inspect zoe-llamacpp | grep MODEL_NAME
```

**Update LiteLLM config when changing models:**
1. Edit `services/zoe-litellm/minimal_config.yaml`
2. Update `model:` field in `model_list` section
3. Restart: `docker restart zoe-litellm`

### Adding New Models

**Local Model (via zoe-llamacpp):**
```yaml
- model_name: new-local-model
  litellm_params:
    model: openai//models/path/to/model.gguf
    api_base: http://zoe-llamacpp:11434/v1
    api_key: dummy
    temperature: 0.7
    max_tokens: 512
    timeout: 30
```

**Cloud Model (OpenAI):**
```yaml
- model_name: gpt-4-turbo
  litellm_params:
    model: gpt-4-turbo
    api_key: ${OPENAI_API_KEY}
    temperature: 0.7
    max_tokens: 4000
```

**Cloud Model (Anthropic):**
```yaml
- model_name: claude-opus
  litellm_params:
    model: claude-opus-20250219
    api_key: ${ANTHROPIC_API_KEY}
    temperature: 0.7
    max_tokens: 4000
```

## 🛡️ CRITICAL RULES

### ❌ NEVER DO

1. **Direct llamacpp calls in chat.py**
   - ❌ `http://zoe-llamacpp:11434/v1/chat/completions`
   - ✅ `http://zoe-litellm:8001/v1/chat/completions`

2. **Hardcoded model logic**
   - ❌ `if model == "gemma": use_llamacpp()`
   - ✅ Let LiteLLM route via config

3. **Multiple inference endpoints**
   - ❌ Different URLs for different models
   - ✅ Single LiteLLM endpoint for all

4. **Bypassing the gateway**
   - ❌ Direct API calls to OpenAI/Anthropic
   - ✅ Route through LiteLLM for caching/tracking

### ✅ ALWAYS DO

1. **Use LiteLLM for all LLM calls**
   - Streaming and non-streaming
   - Local and cloud models
   - Production and development

2. **Update config, not code**
   - Model changes → edit `minimal_config.yaml`
   - Restart service: `docker restart zoe-litellm`

3. **Test after config changes**
   ```bash
   # 1. Check service started
   docker logs zoe-litellm --tail 20
   
   # 2. List models
   curl http://zoe-litellm:8001/v1/models -H "Authorization: Bearer <key>"
   
   # 3. Test completion
   curl -X POST http://zoe-litellm:8001/v1/chat/completions \
     -H "Authorization: Bearer <key>" \
     -d '{"model":"local-model","messages":[{"role":"user","content":"test"}]}'
   ```

4. **Monitor logs for errors**
   ```bash
   docker logs -f zoe-litellm
   ```

## 🔧 Troubleshooting

### Service Won't Start

**Check logs:**
```bash
docker logs zoe-litellm 2>&1 | tail -50
```

**Common issues:**
- Config syntax error → Validate YAML
- Missing dependency → Check Dockerfile
- Port conflict → Check `docker ps`

### Model Not Found

**Error:** `Invalid model name passed in model=X`

**Solution:**
1. List available models: `curl http://zoe-litellm:8001/v1/models`
2. Check config has model defined
3. Restart service after config changes

### Connection Refused

**Error:** `Connection refused` from zoe-core

**Check:**
1. Service running: `docker ps | grep litellm`
2. Network connectivity: `docker exec zoe-core ping zoe-litellm`
3. Port correct: Should be 8001

### Slow Responses

**First request slow:**
- Normal! Model loading takes 5-20 seconds
- Subsequent requests cached (10min TTL)

**All requests slow:**
- Check Redis: `docker logs zoe-redis`
- Check llamacpp performance: `docker exec zoe-llamacpp nvidia-smi`

## 📊 Monitoring

### Health Check

```bash
curl http://zoe-litellm:8001/health
```

### Model List

```bash
curl http://zoe-litellm:8001/v1/models \
  -H "Authorization: Bearer <master_key>"
```

### Docker Status

```bash
docker ps --filter "name=zoe-litellm" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

### Service Logs

```bash
# Real-time
docker logs -f zoe-litellm

# Last 50 lines
docker logs zoe-litellm --tail 50

# Errors only
docker logs zoe-litellm 2>&1 | grep -i error
```

## 🎓 Best Practices

1. **Use model aliases:**
   - `local-model` instead of specific model names
   - Easier to swap models without code changes

2. **Configure fallbacks:**
   - Primary: Local model (fast, free)
   - Secondary: Cloud model (slower, costs money)

3. **Monitor costs:**
   - LiteLLM can track usage per model
   - Set up alerts for cloud API usage

4. **Cache aggressively:**
   - 10min TTL is default
   - Increase for stable responses
   - Decrease for dynamic content

5. **Test in development:**
   - Use `local-fast` for quick iterations
   - Switch to `local-model` for quality
   - Use cloud models for complex tasks

## 📚 Related Documentation

- [LiteLLM Development Rules](../governance/LITELLM_RULES.md)
- [Model Configuration Guide](../guides/MODEL_CONFIGURATION.md)
- [llama.cpp Integration](./LLAMACPP_INTEGRATION.md)
- [RouteLLM Documentation](./ROUTELLM.md)

## 🔗 External Resources

- [LiteLLM Documentation](https://docs.litellm.ai/)
- [OpenAI API Reference](https://platform.openai.com/docs/api-reference)
- [LiteLLM GitHub](https://github.com/BerriAI/litellm)

---

**Last Validated:** 2025-11-17  
**Validation Script:** `tools/audit/validate_litellm.sh`

