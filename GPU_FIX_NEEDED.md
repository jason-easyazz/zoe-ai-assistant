# 🚨 GPU Not Being Used - Performance Issue

## Problem Identified

**Current Performance:**
- Token generation speed: 24.5 tokens/sec
- GPU utilization: [N/A] (NOT USING GPU!)
- Running on: **CPU only**

**Expected Performance with GPU:**
- Jetson Orin NX: 50-100+ tokens/sec
- **4x faster** than current

## Root Cause

The llama.cpp server (zoe-llamacpp) is not utilizing the GPU. This could be due to:

1. **GPU layers not configured** - Model not offloaded to GPU
2. **CUDA not enabled** - llama.cpp built without CUDA support
3. **Container GPU access** - Docker container can't see GPU

## Quick Checks Needed

### 1. Check llama.cpp startup logs:
```bash
docker logs zoe-llamacpp 2>&1 | grep -E "CUDA|GPU|ggml" | head -20
```

Look for:
- ✅ "CUDA: 1" or "GGML_CUDA: 1"  
- ✅ "using CUDA"
- ❌ "CUDA not available" or "CPU only"

### 2. Check docker-compose.yml GPU configuration:
```bash
grep -A 10 "zoe-llamacpp:" docker-compose.yml | grep -E "runtime|devices|capabilities"
```

Should have:
```yaml
runtime: nvidia  # or deploy.resources.reservations.devices
```

### 3. Check if Jetson can see GPU:
```bash
nvidia-smi
```

## Quick Fixes

### Fix 1: Enable GPU in docker-compose.yml

If missing, add to zoe-llamacpp service:
```yaml
zoe-llamacpp:
  runtime: nvidia
  environment:
    - NVIDIA_VISIBLE_DEVICES=all
    - NVIDIA_DRIVER_CAPABILITIES=compute,utility
```

OR for newer Docker Compose:
```yaml
zoe-llamacpp:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
```

### Fix 2: Configure GPU layers in llama.cpp

Add to startup command or environment:
```bash
-ngl 99  # Offload 99 layers to GPU (all for 3B model)
```

Or set environment variable:
```yaml
environment:
  - CUDA_VISIBLE_DEVICES=0
  - LLAMA_CUBLAS=1
```

### Fix 3: Use pre-built CUDA image

If llama.cpp not built with CUDA, switch to CUDA-enabled image:
```yaml
image: ghcr.io/ggerganov/llama.cpp:server-cuda  # Instead of :server
```

## Current Quick Win

Even without GPU fix, reduced token limit helps:
- **Before:** 512 tokens at 24.5 t/s = 20.9s
- **After:** 256 tokens at 24.5 t/s = 10.4s  
- **Improvement:** 50% faster

## Expected After GPU Fix

With GPU enabled:
- **Speed:** 256 tokens at 75 t/s = 3.4s
- **Improvement:** 85% faster than current

## Action Items

1. ✅ **DONE:** Reduced token limits (512→256)
2. ⏳ **TODO:** Enable GPU in docker-compose.yml
3. ⏳ **TODO:** Rebuild zoe-llamacpp with GPU support
4. ⏳ **TODO:** Test GPU performance

## Testing After Fix

```bash
# Should see much faster tokens/sec
docker logs zoe-llamacpp --tail 50 | grep "predicted_per_second"

# Should show GPU utilization
docker exec zoe-llamacpp nvidia-smi
```

Expected: 50-100+ tokens/sec (vs current 24.5)

