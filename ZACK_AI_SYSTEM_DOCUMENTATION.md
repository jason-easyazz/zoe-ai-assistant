# Zack AI System - Complete Documentation
Last Updated: $(date)

## 🎯 OVERVIEW
Zack is the lead developer AI with full system access and sophisticated RouteLLM routing.

## ✅ WORKING CONFIGURATION

### 1. API Method Signatures (CRITICAL - DO NOT CHANGE)
```python
# AI Client
ai_client.generate_response(message: str, context: Dict = None) -> Dict
# NO temperature, NO max_tokens parameters!

# RouteLLM Manager
manager.get_model_for_request(message: str = None, context: Dict = None) -> Tuple[str, str]
# Returns (provider, model) based on message complexity
```

### 2. Docker Configuration (REQUIRED)
```yaml
services:
  zoe-core:
    env_file:
      - .env  # MUST have this to load API keys
```

### 3. Environment Variables
Create `.env` file in `/home/pi/zoe/` with:
```
ANTHROPIC_API_KEY=your-key-here
OPENAI_API_KEY=your-key-here
```

### 4. RouteLLM Routing Logic
- Simple queries (Hi, What's 2+2?) → `ollama/llama3.2:3b`
- Complex queries (Architecture, Analysis) → `anthropic/claude-3-haiku-20240307`
- Medium queries → Varies based on availability

## 🚨 COMMON ISSUES AND FIXES

### Issue: "temperature parameter" error
**Cause**: developer.py has extra parameters in ai_client.generate_response()
**Fix**: Remove ALL parameters except (message, context):
```bash
docker exec zoe-core sed -i 's/generate_response(.*temperature.*)/generate_response(prompt)/' /app/routers/developer.py
```

### Issue: API keys not loaded
**Cause**: docker-compose.yml missing env_file
**Fix**: Add to zoe-core service:
```yaml
env_file:
  - .env
```

### Issue: No AI response
**Cause**: AI client is async but not awaited
**Fix**: Ensure all calls use `await`:
```python
response = await ai_client.generate_response(message, context)
```

## 📋 TESTING CHECKLIST

Run these to verify everything works:

```bash
# 1. Check API keys loaded
docker exec zoe-core python3 -c "import os; print('Keys:', bool(os.getenv('ANTHROPIC_API_KEY')))"

# 2. Test RouteLLM
docker exec zoe-core python3 -c "
from llm_models import LLMModelManager
m = LLMModelManager()
print(m.get_model_for_request('Design a system'))
"

# 3. Test Zack
curl -X POST http://localhost:8000/api/developer/chat \
  -d '{"message": "What is our system architecture?"}' | jq '.response'
```

## 🔧 MAINTENANCE

### To Restore if Broken
```bash
# Restore from backup
cp backups/zack_ai_working_*/developer.py services/zoe-core/routers/developer.py
docker restart zoe-core
```

### To Check Status
```bash
curl http://localhost:8000/api/developer/status | jq '.'
```

## ⚠️ DO NOT MODIFY
1. ai_client.generate_response signature
2. The env_file configuration in docker-compose.yml
3. The working developer.py async/await structure

## 📊 SYSTEM CAPABILITIES
- **Providers**: Anthropic, OpenAI, Ollama
- **Models**: 14+ across providers
- **Routing**: Intelligent based on query complexity
- **Fallback**: Always falls back to Ollama if API fails
