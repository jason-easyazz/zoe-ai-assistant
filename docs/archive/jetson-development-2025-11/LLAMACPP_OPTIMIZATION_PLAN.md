# LLAMA.CPP OPTIMIZATION PLAN FOR REAL-TIME VOICE
**Target:** Qwen 2.5 7B with 20+ tok/s for natural voice conversations  
**Date:** 2025-11-13

---

## 🎯 OPTIMIZATION GOALS

### Performance Targets
- **Token Generation:** 20+ tok/s (vs current 5 tok/s)
- **First Token Latency:** <500ms
- **GPU Utilization:** 70-90%
- **Voice Response Time:** <2s total (STT → LLM → TTS)

### Model Selection
- **Model:** Qwen 2.5 7B Instruct
- **Quantization:** Q4_K_M (balance of speed/quality) or Q5_K_M if GPU allows
- **Size:** ~4.4GB (Q4_K_M)

---

## 🔧 OPTIMIZATION STRATEGY

### 1. Model Download & Selection
```bash
# Download Qwen 2.5 7B Instruct Q4_K_M
huggingface-cli download \
  bartowski/Qwen2.5-7B-Instruct-GGUF \
  Qwen2.5-7B-Instruct-Q4_K_M.gguf \
  --local-dir /home/zoe/assistant/models/qwen2.5-7b-gguf
```

**Alternative Quantizations (if needed):**
- `Q4_0` - Fastest, lowest quality (~3.6GB)
- `Q4_K_M` - Balanced (recommended, ~4.4GB)
- `Q5_K_M` - Higher quality, slightly slower (~5.2GB)

### 2. llama.cpp Server Optimization

**Current Config (SLOW):**
```yaml
CTX_SIZE: 4096
N_GPU_LAYERS: 99
THREADS: 6
PARALLEL: 4
```

**Optimized Config (FAST):**
```yaml
CTX_SIZE: 2048          # Reduce context for speed (voice doesn't need 4K)
N_GPU_LAYERS: 99        # All layers on GPU
THREADS: 8              # More CPU threads for parallel work
PARALLEL: 8             # More parallel sequences
N_BATCH: 512            # Larger batch for prompt processing
N_UBATCH: 256           # Micro-batch for generation
CONT_BATCHING: true     # Enable continuous batching
FLASH_ATTN: true        # Flash attention for speed
```

**Additional Flags:**
```bash
--n-predict 512         # Limit max generation
--mlock                 # Lock model in RAM (prevent swapping)
--no-mmap               # Load entirely to RAM (faster)
--numa isolate          # NUMA optimization (if multi-socket)
```

### 3. GPU Optimization

**Jetson-Specific Settings:**
```bash
# Max performance mode
sudo nvpmodel -m 0
sudo jetson_clocks

# Increase GPU clock
echo "Verifying GPU is in MAX mode"
sudo cat /sys/devices/gpu.0/devfreq/17000000.gpu/cur_freq

# Monitor during load
tegrastats --interval 500
```

**CUDA Settings:**
```bash
export CUDA_LAUNCH_BLOCKING=0        # Async CUDA (faster)
export CUDA_VISIBLE_DEVICES=0
```

### 4. Voice Pipeline Optimization

**Total Voice Latency Breakdown:**
1. **STT (Whisper):** 200-500ms
2. **LLM (Qwen 2.5 7B):** <1000ms (TARGET)
3. **TTS:** 300-600ms
4. **Network:** 50-100ms

**Target Total:** <2s (excellent for voice)

**Optimizations:**
- Stream LLM tokens to TTS (don't wait for full response)
- Pre-warm model (keep loaded, no cold start)
- Use smaller context window (2048 vs 4096)
- Enable continuous batching for multiple users

### 5. Model Warm-up Strategy

```python
# Pre-load and warm-up on startup
async def warm_up_model():
    # Send dummy prompt to load model into GPU
    await llm_generate("Hi", max_tokens=10)
    # Model now cached in GPU memory
```

---

## 📊 BENCHMARKING STRATEGY

### Test Cases
1. **Simple Query:** "What's the weather?"
2. **Voice Query:** "Tell me about quantum physics"
3. **Code Query:** "Write a Python function"
4. **Rapid Fire:** 10 consecutive voice queries

### Metrics to Capture
- Prompt tokens/sec (should be 400-600 tok/s)
- Generation tokens/sec (TARGET: 20+ tok/s)
- First token latency (TARGET: <500ms)
- GPU utilization % (TARGET: 70-90%)
- Memory usage (should be <13GB)

### Tools
```bash
# Monitor GPU during benchmark
watch -n 0.5 nvidia-smi

# Monitor Jetson stats
tegrastats --interval 500

# HTTP benchmark
curl -X POST http://localhost:11434/v1/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5-7b","prompt":"Test","max_tokens":50,"stream":true}'
```

---

## 🚀 IMPLEMENTATION STEPS

### Phase 1: Download & Configure (15 min)
1. Download Qwen 2.5 7B Q4_K_M GGUF
2. Update docker-compose.yml with new model path
3. Update entrypoint.sh with optimized parameters

### Phase 2: Optimize (10 min)
1. Set Jetson to max performance mode
2. Apply CUDA optimizations
3. Configure llama.cpp with aggressive settings

### Phase 3: Test & Benchmark (15 min)
1. Run startup test (verify model loads)
2. Run benchmark suite
3. Measure GPU utilization
4. Test voice pipeline end-to-end

### Phase 4: Fine-tune (10 min)
1. Adjust batch sizes based on results
2. Tune parallel sequences
3. Optimize context size
4. Re-benchmark and validate

---

## 🎯 SUCCESS CRITERIA

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| Generation Speed | 5 tok/s | 20+ tok/s | ⏳ |
| First Token | N/A | <500ms | ⏳ |
| GPU Utilization | Unknown | 70-90% | ⏳ |
| Voice Latency | N/A | <2s total | ⏳ |
| Model Quality | 3B | 7B (Qwen) | ⏳ |

---

## ⚠️ RISK MITIGATION

**Risk 1: Memory Overflow (7B > 3B)**
- **Solution:** Use Q4_K_M (not Q5), reduce context to 2048
- **Fallback:** Keep Llama 3.2 3B as backup

**Risk 2: Slower than 3B**
- **Solution:** Aggressive batching, GPU layers, mlock
- **Fallback:** Use Q4_0 (faster, lower quality)

**Risk 3: GPU memory exhaustion**
- **Solution:** Monitor with `nvidia-smi`, reduce parallel sequences
- **Fallback:** Reduce n-gpu-layers to 32 (partial offload)

---

## 🔄 ROLLBACK PLAN

If Qwen 2.5 7B is too slow:
1. Keep Llama 3.2 3B as fallback
2. Use `LLM_PROVIDER` env var to switch
3. Git tag: `pre-qwen-optimization`

---

## 📝 NEXT STEPS

1. ✅ Create optimization plan (this document)
2. ⏳ Download Qwen 2.5 7B GGUF
3. ⏳ Update llama.cpp configuration
4. ⏳ Set Jetson to max performance
5. ⏳ Benchmark and tune
6. ⏳ Test voice pipeline end-to-end

**Estimated Time:** 50 minutes  
**Expected Outcome:** 4x speed improvement (5 → 20+ tok/s)





