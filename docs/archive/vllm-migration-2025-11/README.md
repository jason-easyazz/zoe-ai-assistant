# vLLM Migration Archive - November 2025

**Status:** BLOCKED - Migration Failed  
**Date:** 2025-11-10 to 2025-11-13  
**Outcome:** Switched to llama.cpp instead

---

## Summary

Attempted to migrate from Ollama to vLLM for better performance on Jetson Orin NX 16GB.

**Result:** BLOCKED by fundamental PyTorch CUDA allocator incompatibility with Jetson R36.4.3

**Error:** `RuntimeError: NVML_SUCCESS == r INTERNAL ASSERT FAILED at c10/cuda/CUDACachingAllocator.cpp:1131`

---

## What Was Tried

1. **Multiple vLLM versions:**
   - dustynv/vllm:r36.4-cu129-24.04
   - NVIDIA official containers
   - Built from source

2. **Configuration attempts (8+ hours):**
   - Various PYTORCH_CUDA_ALLOC_CONF settings
   - CUDA_LAUNCH_BLOCKING=1
   - --enforce-eager mode
   - --disable-custom-all-reduce
   - Different swap sizes (4GB, 8GB, 16GB)
   - GC settings (0.9 aggressive, 0.95 very aggressive)

3. **Different models:**
   - Llama-3.2-3B-Instruct-AWQ
   - Qwen2.5-Coder-7B-AWQ

**None worked.** All crashed with same allocator error.

---

## Root Cause

PyTorch CUDA allocator (c10) incompatible with Jetson's unified memory architecture in R36.4.3.

This is a fundamental issue in PyTorch, not a configuration problem.

---

## Solution

**Switched to llama.cpp with GGUF models:**
- ✅ Works perfectly on Jetson
- ✅ Better performance (13.55 tok/s)
- ✅ Lower memory (2.3GB vs 4GB+)
- ✅ More stable
- ✅ Easier to configure

See: `LLAMACPP_PERFORMANCE_REPORT.md`

---

## Files Archived

- VLLM_MIGRATION_STATUS.md
- VLLM_BUILD_CHALLENGES.md
- VLLM_EXHAUSTIVE_DEBUG_SUMMARY.md
- VLLM_MIGRATION_SUMMARY.md
- VLLM_MIGRATION_BLOCKED.md
- vllm-debug-log.md
- VLLM_PRODUCTION_ARCHITECTURE.md (if existed)

---

## For Future Reference

If attempting vLLM on Jetson again:
1. Check if PyTorch CUDA allocator issue is fixed
2. Verify JetPack version compatibility
3. Test with minimal config first
4. Consider TensorRT-LLM as alternative

**Current Recommendation:** Use llama.cpp for Jetson deployments.

---

**Archive Date:** 2025-11-13  
**Archived By:** Zoe AI Assistant  
**Reason:** Migration failed, switched to llama.cpp successfully





