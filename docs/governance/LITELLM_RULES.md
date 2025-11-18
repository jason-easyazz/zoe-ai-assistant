# LiteLLM Gateway - Development Rules & Guidelines

**Status:** 🔒 MANDATORY  
**Enforcement:** Pre-commit hooks + Code review  
**Last Updated:** 2025-11-17

---

## 🎯 Golden Rule

> **LiteLLM Gateway (`zoe-litellm:8001`) is the ONLY way to call LLMs in production.**
> 
> No exceptions. No "temporary" direct calls. No "just this one time."

---

## ❌ FORBIDDEN PATTERNS

### 1. Direct LLM Service Calls

**NEVER** call inference services directly:

```python
# ❌ WRONG - Direct llamacpp call
response = await client.post("http://zoe-llamacpp:11434/v1/chat/completions", ...)

# ❌ WRONG - Direct OpenAI call  
response = await client.post("https://api.openai.com/v1/chat/completions", ...)

# ❌ WRONG - Direct Anthropic call
response = await client.post("https://api.anthropic.com/v1/messages", ...)

# ✅ CORRECT - Through LiteLLM Gateway
response = await client.post("http://zoe-litellm:8001/v1/chat/completions", ...)
```

**Why:** Bypassing the gateway loses caching, fallbacks, monitoring, and cost control.

### 2. Hardcoded Model Logic

**NEVER** hardcode model selection in code:

```python
# ❌ WRONG - Hardcoded model routing
if task_type == "coding":
    model = "gpt-4"
    url = "https://api.openai.com/..."
elif task_type == "chat":
    model = "local-model"
    url = "http://zoe-llamacpp:..."

# ✅ CORRECT - Let LiteLLM route via config
response = await client.post(
    "http://zoe-litellm:8001/v1/chat/completions",
    json={"model": "function-calling", ...}  # Uses model_group_alias from config
)
```

**Why:** Model changes should be config changes, not code deployments.

### 3. Multiple Inference Endpoints

**NEVER** maintain separate endpoints for different models:

```python
# ❌ WRONG - Multiple endpoints
OPENAI_URL = "https://api.openai.com/..."
ANTHROPIC_URL = "https://api.anthropic.com/..."
LOCAL_URL = "http://zoe-llamacpp:..."

# ✅ CORRECT - Single gateway endpoint
LLM_URL = "http://zoe-litellm:8001/v1/chat/completions"
```

**Why:** Increases complexity, breaks caching, prevents unified monitoring.

### 4. Inline Model Configuration

**NEVER** configure models in Python code:

```python
# ❌ WRONG - Model config in code
MODELS = {
    "fast": {"url": "...", "max_tokens": 128},
    "powerful": {"url": "...", "max_tokens": 4096}
}

# ✅ CORRECT - Models configured in minimal_config.yaml
# Python just references model names
model = "fast-model"  # Defined in config
```

**Why:** Configuration belongs in config files, not code.

### 5. Custom Auth/Retry Logic

**NEVER** implement your own retry/fallback logic:

```python
# ❌ WRONG - Custom retry logic
for attempt in range(3):
    try:
        response = await call_llm(...)
        break
    except:
        if attempt < 2:
            await asyncio.sleep(2 ** attempt)
        else:
            # Try different model
            response = await call_different_llm(...)

# ✅ CORRECT - LiteLLM handles retries & fallbacks automatically
response = await client.post("http://zoe-litellm:8001/v1/chat/completions", ...)
```

**Why:** LiteLLM has battle-tested retry logic and automatic fallbacks.

---

## ✅ REQUIRED PATTERNS

### 1. Always Use LiteLLMProvider

```python
from llm_provider import get_llm_provider

# ✅ CORRECT - Get default provider (LiteLLM)
provider = get_llm_provider()
response = await provider.generate(prompt, model="local-model")

# ✅ CORRECT - Explicit LiteLLM
provider = get_llm_provider(force_provider="litellm")
```

### 2. Use Model Aliases

```python
# ✅ CORRECT - Semantic aliases from config
models = {
    "fast": "fast-model",        # Maps to local-fast in config
    "smart": "function-calling",  # Maps to local-model or gpt-4o-mini
    "powerful": "powerful"        # Maps to claude-3-5-sonnet
}

response = await call_litellm(model=models["fast"], ...)
```

### 3. Include Master Key

```python
# ✅ CORRECT - Always authenticate
import os
LITELLM_KEY = "sk-f3320300bb32df8f176495bb888ba7c8f87a0d01c2371b50f767b9ead154175f"

headers = {
    "Authorization": f"Bearer {LITELLM_KEY}",
    "Content-Type": "application/json"
}
```

### 4. Handle Streaming Properly

```python
# ✅ CORRECT - OpenAI SSE format
async for line in response.aiter_lines():
    if line.startswith("data: "):
        data = line[6:]  # Remove "data: " prefix
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        if "choices" in chunk and len(chunk["choices"]) > 0:
            delta = chunk["choices"][0].get("delta", {})
            content = delta.get("content", "")
            print(content, end="")
```

### 5. Test Configuration Changes

```python
# ✅ CORRECT - Validation before deployment
def validate_litellm_config():
    """Validate LiteLLM configuration before deploying."""
    response = requests.get(
        "http://zoe-litellm:8001/v1/models",
        headers={"Authorization": f"Bearer {LITELLM_KEY}"}
    )
    assert response.status_code == 200
    models = response.json()["data"]
    assert len(models) > 0
    print(f"✅ LiteLLM configured with {len(models)} models")
```

---

## 🔧 Configuration Management

### File Location

```
services/zoe-litellm/minimal_config.yaml
```

**Mounted as volume:** ✅ Read-only  
**Changes require:** Service restart (`docker restart zoe-litellm`)

### Safe Configuration Updates

```bash
# 1. Backup current config
cp services/zoe-litellm/minimal_config.yaml services/zoe-litellm/minimal_config.yaml.backup

# 2. Edit configuration
nano services/zoe-litellm/minimal_config.yaml

# 3. Validate YAML syntax
python3 -c "import yaml; yaml.safe_load(open('services/zoe-litellm/minimal_config.yaml'))"

# 4. Restart service
docker restart zoe-litellm

# 5. Check startup logs
docker logs zoe-litellm --tail 20

# 6. Validate service health
curl http://zoe-litellm:8001/health

# 7. Test model list
curl http://zoe-litellm:8001/v1/models -H "Authorization: Bearer <key>"

# 8. Test completion
curl -X POST http://zoe-litellm:8001/v1/chat/completions \
  -H "Authorization: Bearer <key>" \
  -d '{"model":"local-model","messages":[{"role":"user","content":"test"}]}'
```

### Adding a New Model

**Local Model (llama.cpp):**

```yaml
- model_name: my-new-model
  litellm_params:
    model: openai//models/my-model/model.gguf
    api_base: http://zoe-llamacpp:11434/v1
    api_key: dummy
    temperature: 0.7
    max_tokens: 512
    timeout: 30
```

**Cloud Model:**

```yaml
- model_name: gpt-4-turbo
  litellm_params:
    model: gpt-4-turbo-preview
    api_key: ${OPENAI_API_KEY}
    temperature: 0.7
    max_tokens: 4000
```

**IMPORTANT:** 
- llama.cpp loads ONE model at a time
- Update `model:` path to match loaded model
- Check with: `docker inspect zoe-llamacpp | grep MODEL`

---

## 🚨 Breaking Changes Protocol

If you MUST bypass LiteLLM (emergency only):

### 1. Document the Reason

Create an issue explaining:
- Why LiteLLM cannot be used
- What is being bypassed
- When it will be fixed
- Technical debt created

### 2. Add TODO Comments

```python
# TODO: TECH DEBT - Direct LLM call bypassing gateway
# Issue: #123 - LiteLLM doesn't support feature X
# Remove after: LiteLLM v2.0 adds feature X
# Contact: @architecture-team before changing
response = await direct_llm_call(...)  # TEMPORARY - DO NOT COPY
```

### 3. Create Tracking Task

```markdown
## TECH DEBT: Direct LLM Call in module_name.py

**Created:** 2025-11-17  
**Severity:** HIGH  
**Impact:** Bypasses caching, fallbacks, monitoring

**Resolution:**
- [ ] LiteLLM adds feature X
- [ ] Update to use gateway
- [ ] Remove direct call
- [ ] Test end-to-end
```

### 4. Time-box the Fix

- Emergency bypass: **Maximum 48 hours**
- Planned bypass: **Maximum 2 weeks**
- After deadline: **Blocks all new features**

---

## 🛡️ Pre-commit Validation

Run validation script before every commit:

```bash
bash tools/audit/validate_litellm.sh
```

**Checks:**
- ✅ No direct inference service calls
- ✅ All LLM calls go through gateway
- ✅ Configuration file is valid YAML
- ✅ Service is healthy
- ✅ Models are accessible

**Failures block commit.**

---

## 📋 Code Review Checklist

Reviewers MUST verify:

- [ ] All LLM calls use `http://zoe-litellm:8001/v1/chat/completions`
- [ ] No hardcoded model URLs or logic
- [ ] Uses `LiteLLMProvider` from `llm_provider.py`
- [ ] Model changes are config changes, not code changes
- [ ] Includes proper error handling
- [ ] Tests pass with LiteLLM gateway
- [ ] Documentation updated if adding models
- [ ] No direct OpenAI/Anthropic API calls

**Any violation = REJECT + explanation**

---

## 🎓 Training Resources

### New Developers

1. Read: `docs/architecture/LITELLM_INTEGRATION.md`
2. Run: `bash tools/audit/validate_litellm.sh`
3. Test: Send request through gateway
4. Review: Example in `services/zoe-core/routers/chat.py`

### Quick Reference

```python
# Standard pattern for all LLM calls
from llm_provider import get_llm_provider

provider = get_llm_provider()  # Returns LiteLLMProvider by default
response = await provider.generate(
    prompt="Your prompt here",
    model="local-model",  # From minimal_config.yaml
    temperature=0.7,
    max_tokens=512
)
```

---

## 🔍 Monitoring & Debugging

### Check Gateway Status

```bash
# Service health
docker ps --filter "name=zoe-litellm"

# Logs (real-time)
docker logs -f zoe-litellm

# Available models
curl http://zoe-litellm:8001/v1/models \
  -H "Authorization: Bearer <master_key>"
```

### Debug Request Issues

```bash
# Test gateway directly
curl -v -X POST http://zoe-litellm:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <master_key>" \
  -d '{
    "model": "local-model",
    "messages": [{"role": "user", "content": "test"}],
    "max_tokens": 10
  }'

# Check network connectivity
docker exec zoe-core ping -c 3 zoe-litellm

# Verify Redis caching
docker exec zoe-redis redis-cli KEYS "*litellm*"
```

---

## 📊 Metrics & Observability

### Track These Metrics

1. **Request Latency**
   - P50, P95, P99 response times
   - Target: P95 < 2s for cached, < 30s for uncached

2. **Cache Hit Rate**
   - Redis cache effectiveness
   - Target: > 40% hit rate

3. **Model Usage**
   - Requests per model
   - Track costs (cloud models)

4. **Error Rate**
   - Failed requests / total requests
   - Target: < 1% error rate

5. **Fallback Frequency**
   - How often fallbacks are triggered
   - Investigate if > 5%

---

## 🚀 Deployment Checklist

Before deploying changes:

- [ ] Validated config syntax (YAML)
- [ ] Tested locally with `docker restart zoe-litellm`
- [ ] Checked startup logs for errors
- [ ] Verified models list endpoint
- [ ] Tested completion endpoint
- [ ] Run validation script: `bash tools/audit/validate_litellm.sh`
- [ ] Updated documentation if needed
- [ ] Code review approved
- [ ] Staging environment tested (if available)

---

## 📞 Support & Questions

### Common Issues

**Q: Model not found error?**  
A: Check config has model defined, restart service

**Q: Connection refused?**  
A: Verify service running: `docker ps | grep litellm`

**Q: Slow responses?**  
A: First request loads model (normal), subsequent cached

**Q: Need to add a model?**  
A: Edit `minimal_config.yaml`, restart service

### Escalation Path

1. Check docs: `docs/architecture/LITELLM_INTEGRATION.md`
2. Run validation: `bash tools/audit/validate_litellm.sh`
3. Check logs: `docker logs zoe-litellm`
4. Ask in #architecture channel
5. Create issue with reproduction steps

---

**Last Updated:** 2025-11-17  
**Next Review:** 2025-12-17 (monthly)  
**Owner:** Architecture Team  
**Enforcement:** Automated + Code Review

