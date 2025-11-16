# vLLM Jetson Debugging Log

## System Info
- Hardware: Jetson Orin NX 16GB
- JetPack: R36 (release), REVISION: 4.3, GCID: 38968081
- CUDA: 12.6 (Driver 540.4.0)
- Docker: Verified and cleaned
- Models: llama-3.2-3b-awq (2.2GB), qwen2.5-coder-7b-awq (5.2GB)

## Pre-Flight Checks
- ✅ GPU visible (Orin NX)
- ✅ JetPack R36.4.3 confirmed
- ✅ 16GB RAM available
- ✅ AWQ models present and verified
- ✅ Docker cleaned (3.6GB reclaimed)
- ✅ Git checkpoint created: vllm-debug-start-20251112-2024

## Phase 1: System Optimization
- ⚠️ nvpmodel/jetson_clocks: Requires sudo (skipped, system at default performance)
- ✅ Swap: 7.6GB already available (sufficient)
- ✅ File descriptors: Increased to 65536
- ✅ CUDA artifacts: Cleared
- ✅ GPU state: Clean (0MB used)

## Attempts

### Attempt #1: Config V1 - expandable_segments + enforce-eager

**Date/Time:** 2025-11-12 12:39  
**Duration:** ~40 seconds (crashed during KV cache allocation)

**Configuration:**
- Container: dustynv/vllm:r36.4-cu129-24.04
- Model: llama-3.2-3b-awq
- Entrypoint: entrypoint-jetson-v1.sh
- PYTORCH_CUDA_ALLOC_CONF: expandable_segments:True,max_split_size_mb:128
- Key vLLM flags: --enforce-eager, --disable-custom-all-reduce, --gpu-memory-utilization 0.65

**Result:** ❌ FAILURE

**Error:**
```
RuntimeError: NVML_SUCCESS == r INTERNAL ASSERT FAILED at "/opt/pytorch/c10/cuda/CUDACachingAllocator.cpp":1131
```

**Failed at:** KV cache allocation (`torch.zeros()` in cache_engine.py:96)

**Next Step:** Try Config V2 (AWQ-Marlin backend)

---

### Attempt #2: Config V2 - AWQ-Marlin + aggressive GC

**Date/Time:** 2025-11-12 12:41  
**Duration:** Immediate crash on import

**Configuration:**
- Container: dustynv/vllm:r36.4-cu129-24.04
- Model: llama-3.2-3b-awq
- Entrypoint: entrypoint-jetson-v2.sh
- PYTORCH_CUDA_ALLOC_CONF: expandable_segments:True,garbage_collection_threshold:0.5,max_split_size_mb:64,roundrobin_alloc:True
- Quantization: awq_marlin

**Result:** ❌ FAILURE

**Error:**
```
RuntimeError: Unrecognized CachingAllocator option: roundrobin_alloc
```

**Failed at:** PyTorch CUDA initialization (module import stage)

**Analysis:** The `roundrobin_alloc` option is not recognized by PyTorch 2.x on this platform

**Next Step:** Try Config V3 (ultra conservative, no caching)

---

### Attempt #3: Config V3 - Ultra conservative, no caching

**Date/Time:** 2025-11-12 12:42  
**Duration:** ~50 seconds (crashed during KV cache allocation)

**Configuration:**
- Container: dustynv/vllm:r36.4-cu129-24.04
- Model: llama-3.2-3b-awq
- Entrypoint: entrypoint-jetson-v3.sh
- PYTORCH_NO_CUDA_MEMORY_CACHING: 1 (caching allocator DISABLED)
- PYTORCH_CUDA_ALLOC_CONF: expandable_segments:True
- Key vLLM flags: --enforce-eager, --gpu-memory-utilization 0.50, --cpu-offload-gb 2, --kv-cache-dtype fp8

**Result:** ❌ FAILURE (Different error!)

**Error:**
```
torch.AcceleratorError: CUDA error: out of memory
```

**Failed at:** KV cache allocation (`torch.zeros()` in cache_engine.py:96)

**Analysis:** 
- Bypassed CUDA allocator bug by disabling caching
- Hit simple OOM instead
- Config V3 too conservative (50% GPU, 2GB CPU offload, 8GB swap)
- Successfully loaded model before failure

**CHECKPOINT 1 REACHED:** All 3 configs failed
**Next Step:** Phase 4 - Try alternative model (Qwen2.5-Coder-7B)

---

### Attempt #4: Qwen2.5-Coder-7B with Config V1

**Date/Time:** 2025-11-12 12:44  
**Duration:** ~45 seconds (crashed during KV cache allocation)

**Configuration:**
- Container: dustynv/vllm:r36.4-cu129-24.04
- Model: qwen2.5-coder-7b-awq (different architecture, 7B vs 3B)
- Entrypoint: entrypoint-jetson-v1.sh
- PYTORCH_CUDA_ALLOC_CONF: expandable_segments:True,max_split_size_mb:128

**Result:** ❌ FAILURE (Same CUDA allocator bug)

**Error:**
```
RuntimeError: NVML_SUCCESS == r INTERNAL ASSERT FAILED at "/opt/pytorch/c10/cuda/CUDACachingAllocator.cpp":1131
```

**Failed at:** KV cache allocation (`torch.zeros()` in cache_engine.py:96)

**Analysis:**
- Different model architecture (Qwen vs Llama)
- Larger model (7B vs 3B params)
- Same failure point and error as Llama-3.2-3B
- Bug is model-agnostic

**CHECKPOINT 2 REACHED:** All configs and all models failed
**Conclusion:** vLLM v0.9.3 has fundamental PyTorch CUDA allocator incompatibility with Jetson Orin NX R36.4.3

