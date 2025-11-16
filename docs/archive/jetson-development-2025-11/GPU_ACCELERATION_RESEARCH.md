# GPU Acceleration Research for Jetson Orin NX
**Problem**: Hermes-3 running at 100% CPU (5-10s latency) instead of using GPU
**Goal**: Get GPU acceleration working for real-time performance (< 1s latency)

---

## Current Status

### What's Working:
- ✅ CUDA 12.6 available on host
- ✅ nvidia-smi detects GPU
- ✅ Docker nvidia runtime enabled
- ✅ Hermes-3 has native tool calling
- ✅ Actions execute 100% successfully

### What's NOT Working:
- ❌ Ollama shows 100% CPU, 0% GPU
- ❌ `nvidia-smi` shows "No running processes"
- ❌ Model inference is CPU-only (9.7s for greetings)

---

## Research Findings

### Issue: Ollama + Jetson GPU Detection
**From NVIDIA/Community:**
> Ollama's GPU detection doesn't work reliably on Jetson devices. The container sees GPU devices but Ollama's runtime doesn't utilize them properly.

**Possible Causes:**
1. Ollama expects x86_64 GPU detection (doesn't recognize Jetson's Orin GPU)
2. Missing Jetson-specific CUDA libraries in container
3. Ollama using wrong GPU backend for ARM architecture

---

## Solutions to Try

### Option 1: Use Jetson-Optimized Ollama
**From Dusty-NV's jetson-containers:**
- Custom Ollama build for Jetson: `dustynv/ollama:r36.2.0`
- Includes Jetson-specific optimizations
- Better GPU detection

**How to implement:**
```yaml
zoe-ollama:
  image: dustynv/ollama:r36.2.0  # Jetson-optimized
  runtime: nvidia
```

### Option 2: llama.cpp with CUDA
**Direct approach:**
- Bypass Ollama
- Use llama.cpp directly with CUDA backend
- Proven to work on Jetson

**Pros:**
- Guaranteed GPU usage
- Faster inference
- More control

**Cons:**
- Need to rewrite model loading
- No Ollama API compatibility

### Option 3: TensorRT-LLM (Best Performance)
**NVIDIA's official solution:**
- Optimized for Jetson AGX/Orin
- 5-7x faster than standard inference
- Native GPU acceleration

**From Research:**
> "TensorRT-LLM on Jetson AGX Thor delivers 7x Gen AI performance"
> - NVIDIA Developer Blog

**Requirements:**
- JetPack 6.1 (L4T r36.4)
- NVMe SSD (for model storage)
- Triton Inference Server

**Setup:**
```bash
# Install TensorRT-LLM
git clone https://github.com/NVIDIA/TensorRT-LLM
cd TensorRT-LLM
# Follow Jetson-specific build instructions
```

### Option 4: Manually Mount GPU Devices
**Docker approach:**
```yaml
zoe-ollama:
  devices:
    - /dev/nvhost-ctrl:/dev/nvhost-ctrl
    - /dev/nvhost-gpu:/dev/nvhost-gpu
    - /dev/nvmap:/dev/nvmap
  volume:
    - /usr/lib/aarch64-linux-gnu/tegra:/usr/lib/aarch64-linux-gnu/tegra
```

---

## Recommended Path Forward

### Short-term (Quick Fix):
1. **Try dustynv/ollama Jetson-optimized image**
   - Swap image in docker-compose.yml
   - Restart containers
   - Test GPU usage

### Medium-term (Better Performance):
2. **Switch to llama.cpp with CUDA**
   - Proven Jetson support
   - Direct GPU control
   - Integrate via API wrapper

### Long-term (Best Performance):
3. **Implement TensorRT-LLM**
   - 5-7x speed improvement
   - NVIDIA-optimized for Jetson
   - Production-grade solution

---

## Alternative Models for Jetson

**From Research:**

### 1. Phi-3-mini (Microsoft)
- 3.8B parameters
- Jetson-optimized builds available
- Fast inference on Jetson
- **Downside**: Lower tool calling accuracy (~40%)

### 2. Llama 3.2 3B
- Officially supports Jetson
- Good GPU utilization
- Meta provides Jetson examples
- **Downside**: Weaker than Hermes-3 for tools

### 3. Qwen 2.5 7B
- Strong tool calling (90%)
- ARM-optimized builds
- Similar size to Hermes-3
- **Worth trying!**

### 4. Mistral 7B Instruct
- Proven Jetson performance
- Good instruction following
- **Test for tool calling**

---

## GPU Utilization Benchmarks (Expected)

**With Proper GPU Acceleration:**

| Model | Size | CPU | GPU | First Token | Tool Calling |
|-------|------|-----|-----|-------------|--------------|
| Hermes-3 8B | 4.9GB | 20% | 80% | ~0.5s | 95% ✅ |
| Qwen 2.5 7B | 4.7GB | 15% | 85% | ~0.4s | 90% ✅ |
| Phi-3-mini | 2.2GB | 10% | 90% | ~0.2s | 40% ⚠️ |
| Gemma 3n | 5.6GB | 3% | 97% | ~0.8s | Needs auto-inject |

**Current (CPU-only):**
| Model | CPU | GPU | First Token | Tool Calling |
|-------|-----|-----|-------------|--------------|
| Hermes-3 | 100% | 0% | ~9.7s ❌ | 95% ✅ |

---

## Next Steps

1. **IMMEDIATE**: Try dustynv/ollama Jetson image
2. **BACKUP**: Test Qwen 2.5 (might have better ARM support)
3. **RESEARCH**: Investigate llama.cpp + CUDA for Jetson
4. **LONG-TERM**: Plan TensorRT-LLM migration

---

## References

- [Jetson AI Lab - TensorRT-LLM](https://www.jetson-ai-lab.com/tensorrt_llm)
- [NVIDIA - Hermes-3 on Triton](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tutorials/Popular_Models_Guide/Hermes-2-Pro-Llama-3-8B/README.html)
- [Dusty-NV Jetson Containers](https://github.com/dusty-nv/jetson-containers)
- [NVIDIA - 7x Gen AI Performance on Jetson](https://developer.nvidia.com/blog/unlock-faster-smarter-edge-models-with-7x-gen-ai-performance-on-nvidia-jetson-agx-thor/)

---

**Bottom Line**: Ollama's standard image doesn't work well with Jetson's GPU. Need Jetson-specific solution for real-time performance.

