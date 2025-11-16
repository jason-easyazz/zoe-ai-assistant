# REAL-TIME VOICE OPTIMIZATION STATUS
**Date:** 2025-11-13  
**Goal:** Qwen 2.5 7B with 20+ tok/s for natural voice conversations  
**Current Status:** 🟡 IN PROGRESS

---

## 🎯 OPTIMIZATION OBJECTIVES

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| Model | Llama 3.2 3B | Qwen 2.5 7B | ⏳ Downloading |
| Generation Speed | ~5 tok/s | 20+ tok/s | ⏳ Pending |
| First Token Latency | Unknown | <500ms | ⏳ Pending |
| GPU Utilization | Unknown | 70-90% | ⏳ Pending |
| Voice Latency | Unknown | <2s total | ⏳ Pending |

---

## ✅ COMPLETED

1. **Optimization Plan Created**
   - Target: 20+ tok/s for real-time voice
   - Strategy: Aggressive batching, GPU offload, reduced context
   - Document: `LLAMACPP_OPTIMIZATION_PLAN.md`

2. **Optimized Entrypoint Created**
   - File: `services/zoe-llamacpp/entrypoint-optimized.sh`
   - Settings:
     - Context: 2048 (vs 4096) - faster for voice
     - Threads: 8 (vs 6) - more parallelism
     - Parallel: 8 (vs 4) - concurrent requests
     - Batch: 512 - larger prompt processing
     - Micro-batch: 256 - generation optimization
     - Flags: `--cont-batching`, `--flash-attn`, `--mlock`

3. **Docker Configuration Updated**
   - Model path: Qwen 2.5 7B Q4_K_M
   - Environment variables optimized
   - Entrypoint: Using optimized version

4. **Jetson Optimization Script**
   - File: `scripts/setup/optimize_jetson_performance.sh`
   - Actions: Set MAXN mode, maximize clocks
   - **USER ACTION REQUIRED:** Run with sudo

---

## ⏳ IN PROGRESS

### 1. Qwen 2.5 7B Download
**Status:** Downloading via wget  
**Size:** 4.4GB  
**Quantization:** Q4_K_M (balanced speed/quality)  
**Location:** `/home/zoe/assistant/models/qwen2.5-7b-gguf/`

**Progress Check:**
```bash
ls -lh models/qwen2.5-7b-gguf/*.gguf
# Should show increasing size until ~4.4GB
```

**Download Script:**
```bash
bash scripts/setup/download_qwen_optimized.sh
```

---

## 🔜 PENDING (Once Download Completes)

### 2. Restart llama.cpp Service
```bash
docker-compose restart zoe-llamacpp
docker-compose logs -f zoe-llamacpp
```

### 3. Optimize Jetson Performance
**USER ACTION REQUIRED:**
```bash
sudo bash scripts/setup/optimize_jetson_performance.sh
```
This sets:
- Power mode: MAXN (maximum performance)
- Clocks: Maximized
- GPU: Maximum frequency

### 4. Benchmark Qwen 2.5 7B
```bash
# Test generation speed
curl -X POST http://localhost:11434/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5-7b",
    "prompt": "Write a short story about AI",
    "max_tokens": 100,
    "stream": false
  }'

# Measure tokens/second from response
```

### 5. GPU Utilization Check
```bash
# Monitor during load
watch -n 0.5 nvidia-smi

# Or Jetson-specific
tegrastats --interval 500
```

### 6. Voice Pipeline Integration
- Test STT → LLM → TTS latency
- Optimize for streaming (send tokens to TTS incrementally)
- Target: <2s total response time

---

## 📊 OPTIMIZATION PARAMETERS

### Current llama.cpp Config (OPTIMIZED)
```yaml
MODEL_PATH: /models/qwen2.5-7b-gguf/Qwen2.5-7B-Instruct-Q4_K_M.gguf
MODEL_NAME: qwen2.5-7b
CTX_SIZE: 2048          # Reduced for speed (voice doesn't need 4K)
N_GPU_LAYERS: 99        # All layers on GPU
THREADS: 8              # More CPU threads for parallel work
PARALLEL: 8             # More concurrent sequences
N_BATCH: 512            # Larger batch for prompt processing
N_UBATCH: 256           # Micro-batch for generation
CUDA_LAUNCH_BLOCKING: 0 # Async CUDA (faster)
```

### Additional Flags (in entrypoint)
- `--cont-batching` - Continuous batching for multiple users
- `--flash-attn` - Flash attention for speed
- `--mlock` - Lock model in RAM (prevent swapping)

---

## 🎯 WHY QWEN 2.5 7B?

**Advantages:**
- Better instruction following than Llama 3.2 3B
- Superior code generation
- More natural conversational style
- Better context understanding
- Improved tool calling

**Trade-offs:**
- Larger model (7B vs 3B)
- Requires aggressive optimization
- Target: 20+ tok/s with Q4_K_M quantization

---

## ⚠️ POTENTIAL ISSUES & SOLUTIONS

### Issue 1: Download Slow
- **Solution:** Use resume-capable wget (already done)
- **Fallback:** Manual download from Hugging Face

### Issue 2: OOM (Out of Memory)
- **Solution:** Q4_K_M fits in 16GB with room to spare
- **Fallback:** Use Q4_0 (faster, smaller, lower quality)

### Issue 3: Still <20 tok/s
- **Solutions:**
  - Reduce context to 1024
  - Increase batch size to 1024
  - Try Q4_0 quantization
  - Reduce parallel sequences

### Issue 4: Jetson Power Throttling
- **Solution:** Run `optimize_jetson_performance.sh` with sudo
- **Monitor:** `tegrastats` to verify max clocks

---

## 📝 NEXT ACTIONS

### Immediate (USER):
1. **Monitor Qwen download:**
   ```bash
   watch -n 5 "ls -lh models/qwen2.5-7b-gguf/*.gguf 2>&1 | tail -1"
   ```

2. **Once download complete (file ~4.4GB):**
   ```bash
   docker-compose restart zoe-llamacpp
   docker-compose logs -f zoe-llamacpp
   ```

3. **Optimize Jetson (REQUIRES SUDO):**
   ```bash
   sudo bash scripts/setup/optimize_jetson_performance.sh
   ```

4. **Test & Benchmark:**
   ```bash
   # Simple test
   curl -X POST http://localhost:11434/v1/completions \
     -H "Content-Type: application/json" \
     -d '{"model":"qwen2.5-7b","prompt":"Hi","max_tokens":10}'
   ```

---

## 🚀 EXPECTED OUTCOMES

**After Optimization:**
- Token generation: 20-25 tok/s (4-5x improvement)
- First token: <500ms
- Voice response: <2s total (STT → LLM → TTS)
- GPU utilization: 70-90%
- Natural, fluid conversations

**Success Criteria:**
User can have natural voice conversations with Zoe in real-time,with minimal latency and high-quality responses.

---

**Status:** 🟡 IN PROGRESS (Download ongoing)  
**ETA:** 20-30 minutes (download + test + tune)  
**Created:** 2025-11-13  
**Last Updated:** 2025-11-13





