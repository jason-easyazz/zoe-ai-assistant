# Docker Networking Rules - MANDATORY

**Critical Rule**: All Zoe services MUST be on the SAME Docker network to communicate.

## 🚨 The Problem We Fixed

**Date**: 2025-11-10
**Issue**: Services were on different Docker networks:
- `zoe-core` → `assistant_zoe-network`
- LLM service → `zoe-network`

**Result**: 100% test failure because zoe-core couldn't reach the LLM service
- Error: `[Errno -2] Name or service not known`
- All LLM requests failed → generic greetings returned
- Tests showed 0% success rate

> **Note**: As of Dec 2025, Zoe uses `zoe-llamacpp` (llama.cpp) instead of Ollama for LLM inference.

## ✅ The Solution

### 1. EXPLICIT Network Naming
In `docker-compose.yml`, define network with explicit name to prevent auto-prefixing:

```yaml
networks:
  zoe-network:
    name: zoe-network  # ✅ REQUIRED: Prevents "assistant_" or "zoe_" prefix
    driver: bridge
```

### 2. All Services Use Same Network
Every service MUST specify `zoe-network`:

```yaml
services:
  zoe-core:
    networks:
      - zoe-network
  
  zoe-llamacpp:
    networks:
      - zoe-network
  
  zoe-mcp-server:
    networks:
      - zoe-network
```

## 📋 Validation Checklist

**BEFORE deploying or modifying docker-compose.yml:**

1. ✅ Network has explicit `name:` field
2. ✅ All services specify `networks: [zoe-network]`
3. ✅ Run validation script: `tools/docker/validate_networks.sh`
4. ✅ Test connectivity: `docker exec zoe-core ping -c 2 zoe-llamacpp`

## 🛠️ Validation Commands

### Check Current Networks
```bash
# List all networks
docker network ls

# Check which network each container is on
docker ps --format "{{.Names}}: {{.Networks}}"

# Verify zoe-core can reach zoe-llamacpp
docker exec zoe-core ping -c 2 zoe-llamacpp
```

### Fix Mismatched Networks
```bash
# Connect missing container to zoe-network
docker network connect zoe-network <container-name>

# OR restart with fixed docker-compose.yml
cd /home/zoe/assistant
docker compose down
docker compose up -d
```

## 🚫 NEVER Do This

### ❌ DON'T use different networks
```yaml
# BAD - Different networks will break communication!
services:
  zoe-core:
    networks:
      - assistant_zoe-network  # ❌ WRONG
  
  zoe-llamacpp:
    networks:
      - zoe-network  # ❌ DIFFERENT from zoe-core
```

### ❌ DON'T forget explicit network name
```yaml
# BAD - Docker will add prefix based on directory
networks:
  zoe-network:
    driver: bridge  # ❌ MISSING "name:" field
# Results in: assistant_zoe-network or zoe_zoe-network
```

## 🎯 Why This Matters

**Inter-Service Communication**:
- `zoe-core` → `zoe-llamacpp:11434` (LLM inference via llama.cpp)
- `zoe-core` → `zoe-mcp-server:8003` (Tool calling)
- `zoe-mcp-server` → `zoe-core:8000` (API calls)
- `zoe-mem-agent` → `zoe-core:8000` (Expert coordination)

**If networks don't match:**
- ❌ DNS resolution fails
- ❌ "Name or service not known" errors
- ❌ All dependent features break
- ❌ Silent failures with generic error responses

## 📊 Impact on Tests

**When networks are mismatched:**
- Natural language tests: 0% success
- Tool calling tests: 0% success  
- Integration tests: 0% success
- System appears "working" but produces generic responses

**When networks are correct:**
- Natural language tests: Target 90%+
- Tool calling tests: Target 95%+
- Integration tests: Target 95%+

## 🔄 Pre-Commit Hook

Add to `.git/hooks/pre-commit`:
```bash
#!/bin/bash
# Validate Docker network configuration
if git diff --cached --name-only | grep -q "docker-compose"; then
    echo "🔍 Validating Docker network configuration..."
    
    if ! grep -q 'name: zoe-network' assistant/docker-compose.yml; then
        echo "❌ ERROR: docker-compose.yml missing 'name: zoe-network'"
        echo "   All services must use explicit network name"
        exit 1
    fi
    
    # Check all services use zoe-network
    services_without_network=$(grep -A 20 "^  [a-z]" assistant/docker-compose.yml | \
                               grep -B 20 "networks:" | \
                               grep -v "zoe-network" | \
                               grep "^  [a-z]" || true)
    
    if [ ! -z "$services_without_network" ]; then
        echo "⚠️  WARNING: Some services may not be on zoe-network"
        echo "   Review: $services_without_network"
    fi
    
    echo "✅ Docker network validation passed"
fi
```

## 📖 Related Documentation

- **WiFi/Network Issues**: Same validation applies to host network connectivity
- **Jetson/Pi Deployment**: Network config identical across platforms
- **Docker Troubleshooting**: See `docs/guides/DOCKER_TROUBLESHOOTING.md`

## 🆘 Troubleshooting

### Symptom: "Name or service not known"
**Cause**: Containers on different Docker networks
**Fix**: Run `tools/docker/validate_networks.sh` and fix configuration

### Symptom: Tests failing with 0% success
**Cause**: Likely networking issue preventing service communication
**Fix**: Check `docker logs zoe-core` for connection errors

### Symptom: Generic "Hi there!" responses
**Cause**: LLM service unreachable, falling back to default
**Fix**: Verify `docker exec zoe-core ping zoe-llamacpp` works

## 📌 Summary

**Golden Rule**: ONE network (`zoe-network`) for ALL services, with explicit `name:` field.

**Before ANY docker-compose.yml change:**
1. Validate network configuration
2. Test inter-container connectivity
3. Run test suite to verify

**This prevents hours of debugging mysterious failures.**

