# Platform Comparison Report: Jetson vs Raspberry Pi

**Generated:** 2025-11-09  
**Purpose:** Identify differences between Jetson Orin NX (GPU) and Raspberry Pi 5 (CPU) configurations for unified codebase merge

---

## Executive Summary

**Key Finding:** The ONLY real differences are GPU runtime settings and model selections. Everything else is identical.

**Architecture:** Both platforms are ARM64 with 16GB RAM - perfect for unified codebase.

---

## 1. Docker Compose Differences

### GPU Runtime Settings (Jetson-Specific)

**Services requiring `runtime: nvidia` on Jetson:**

1. **zoe-ollama** (line 260)
   - Jetson: `runtime: nvidia`
   - Pi: No runtime specified (defaults to CPU)

2. **zoe-whisper** (line 279)
   - Jetson: `runtime: nvidia`
   - Pi: No runtime specified

3. **zoe-tts** (line 294)
   - Jetson: `runtime: nvidia`
   - Pi: No runtime specified

4. **zoe-litellm** (line 341)
   - Jetson: `runtime: nvidia`
   - Pi: No runtime specified

### Volume Mount Differences

**zoe-tts service:**
- Jetson: `./services/zoe-tts/samples:/app/samples` (active)
- Pi: Same mount commented out (line 240)

### Additional Services (Jetson Only)

Jetson has two additional services not in Pi base config:
- `zoe-code-execution` (lines 69-95)
- `zoe-mem-agent` (lines 96-126)

**Note:** These may be in Pi's override files or may need to be added to base config.

---

## 2. Model Configuration Differences

### Jetson Models (GPU-Optimized)

**Primary models:**
- `gemma3n-e2b-gpu-fixed` - Default GPU model (4.5B, num_gpu=1)
- `gemma3n-e2b-gpu:latest` - GPU model with allocation issues
- `gemma3n:e4b` - GPU-optimized variant
- `phi3:mini` - Fallback CPU model
- `llama3.2:3b` - Fallback CPU model

**Model selection logic:**
- Prefers GPU models (`gemma3n-e2b-gpu-fixed`)
- Falls back to CPU models if GPU unavailable
- Uses `num_gpu=1` for GPU models, `num_gpu=0` for CPU models

### Pi Models (CPU-Optimized)

**Primary models:**
- `gemma3:1b` - Fast CPU model
- `llama3.2:1b` - Benchmark winner (50% tool call rate)
- `qwen2.5:1.5b` - Fast responses
- `qwen2.5:3b` - Balanced performance
- `qwen2.5:7b` - Primary workhorse
- `qwen3:8b` - Heavy reasoning
- `deepseek-r1:14b` - Complex analysis

**Model selection logic:**
- All models use CPU (`num_gpu=0`)
- Optimized for CPU inference
- Smaller models for faster CPU performance

---

## 3. Environment Variable Differences

### Expected Pi Overrides (CPU)

```yaml
services:
  zoe-ollama:
    environment:
      - OLLAMA_NUM_THREADS=4  # CPU thread optimization
  
  zoe-whisper:
    environment:
      - WHISPER_DEVICE=cpu  # Force CPU mode
```

### Expected Jetson Overrides (GPU)

```yaml
services:
  zoe-ollama:
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
    runtime: nvidia
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              capabilities: [gpu]
  
  zoe-whisper:
    environment:
      - WHISPER_DEVICE=cuda  # Use CUDA
    runtime: nvidia
```

**Note:** Current Jetson config doesn't have explicit `WHISPER_DEVICE` or `NVIDIA_VISIBLE_DEVICES` - these may need to be added.

---

## 4. Path Differences

### Current State

- **Pi:** `/home/zoe/assistant` ✅ (after adding zoe user)
- **Jetson:** `/home/zoe/assistant` ✅ (migrated)

### Unified Path Strategy

**Status:** ✅ **IDENTICAL PATHS** - Both platforms use `/home/zoe/assistant`

This simplifies the setup significantly:
- Same `PROJECT_ROOT` environment variable on both platforms
- Same volume mount paths
- No platform-specific path handling needed
- Single source of truth for project location

---

## 5. What's Identical (100% Shared)

✅ **All UI code** (HTML/CSS/JS)  
✅ **All Python code** (routers, models, logic)  
✅ **Database schemas**  
✅ **Documentation**  
✅ **Tests**  
✅ **Core docker-compose structure** (ports, networks, volumes)  
✅ **Service dependencies**  
✅ **Health checks**  
✅ **Network configs**  

---

## 6. Services Comparison

| Service | Jetson | Pi | Notes |
|---------|--------|----|----|
| zoe-core | ✅ | ✅ | Identical |
| zoe-mcp-server | ✅ | ✅ | Identical |
| zoe-code-execution | ✅ | ❌ | Jetson only |
| zoe-mem-agent | ✅ | ❌ | Jetson only |
| homeassistant | ✅ | ✅ | Identical |
| homeassistant-mcp-bridge | ✅ | ✅ | Identical |
| n8n-mcp-bridge | ✅ | ✅ | Identical |
| zoe-ui | ✅ | ✅ | Identical |
| zoe-ollama | ✅ | ✅ | GPU runtime diff |
| zoe-redis | ✅ | ✅ | Identical |
| zoe-whisper | ✅ | ✅ | GPU runtime diff |
| zoe-tts | ✅ | ✅ | GPU runtime diff |
| zoe-n8n | ✅ | ✅ | Identical |
| zoe-litellm | ✅ | ✅ | GPU runtime diff |
| zoe-auth | ✅ | ✅ | Identical |
| cloudflared | ✅ | ✅ | Identical |
| livekit | ✅ | ✅ | Identical |
| zoe-voice-agent | ✅ | ✅ | Identical |

---

## 7. Recommendations for Unified Codebase

### Base docker-compose.yml

**Should contain:**
- All shared services
- All ports, networks, volumes
- Base environment variables
- NO platform-specific settings

### docker-compose.jetson.yml

**Should contain:**
- `runtime: nvidia` for GPU services
- `NVIDIA_VISIBLE_DEVICES=all` for zoe-ollama
- `WHISPER_DEVICE=cuda` for zoe-whisper
- GPU model preferences in model_config.py

### docker-compose.pi.yml

**Should contain:**
- `OLLAMA_NUM_THREADS=4` for zoe-ollama
- `WHISPER_DEVICE=cpu` for zoe-whisper
- CPU model preferences in model_config.py
- NO runtime settings (defaults to CPU)

### Model Configuration Strategy

**Option 1:** Single model_config.py with platform detection
```python
import os
IS_GPU = os.getenv("CUDA_VISIBLE_DEVICES") is not None

if IS_GPU:
    DEFAULT_MODEL = "gemma3n-e2b-gpu-fixed"
else:
    DEFAULT_MODEL = "llama3.2:1b"
```

**Option 2:** Platform-specific model configs
- `model_config.py` (base)
- `model_config_gpu.py` (Jetson)
- `model_config_cpu.py` (Pi)

**Recommendation:** Option 1 (platform detection) - simpler, single source of truth.

---

## 8. Migration Checklist

### Phase 1: Path Migration ✅
- [x] Move `/home/zoe/zoe` → `/home/zoe/assistant`
- [x] Update docker-compose.yml paths
- [x] Update test files
- [x] Verify services start

### Phase 2: Comparison ✅
- [x] Clone Pi repo
- [x] Compare docker-compose files
- [x] Compare model configs
- [x] Generate comparison report

### Phase 3: Create Overrides (Next)
- [ ] Extract GPU configs to `docker-compose.jetson.yml`
- [ ] Extract CPU configs to `docker-compose.pi.yml`
- [ ] Clean base `docker-compose.yml` (remove GPU settings)
- [ ] Update model_config.py with platform detection
- [ ] Test on both platforms
- [ ] Document usage in README

---

## 9. Critical Differences Summary

| Category | Jetson | Pi |
|----------|--------|----|
| **Runtime** | `nvidia` (4 services) | None (CPU default) |
| **Models** | GPU-optimized (`gemma3n-e2b-gpu-fixed`) | CPU-optimized (`llama3.2:1b`) |
| **Ollama** | GPU acceleration | CPU threads |
| **Whisper** | CUDA device | CPU device |
| **Path** | `/home/zoe/assistant` | `/home/pi/zoe` |
| **Services** | +code-execution, +mem-agent | Base only |

---

## 10. Next Steps

1. **Create override files** (Phase 3)
2. **Test on Jetson** with `docker-compose -f docker-compose.yml -f docker-compose.jetson.yml up`
3. **Test on Pi** with `docker-compose -f docker-compose.yml -f docker-compose.pi.yml up`
4. **Merge to main branch** once verified
5. **Update documentation** with platform-specific instructions

---

**Status:** ✅ Phase 1 & 2 Complete | 🔄 Phase 3 In Progress

