# 🎯 Candidate Models Analysis for Jetson Orin NX Voice AI

**Date:** 2025-11-13  
**Goal:** Find models FASTER than Llama 3.2 3B (27 tok/s) for real-time voice  
**Target:** 30-40+ tok/s for sub-1.5s responses

---

## 🏆 TOP 5 PRIORITY CANDIDATES (Most Promising)

### 1. **SmolLM2-1.7B-Instruct** ⭐⭐⭐⭐⭐
- **Size:** 1.7B parameters (~1GB GGUF Q4_K_M)
- **Why:** 50% smaller than Llama 3.2 3B = potentially 40-50 tok/s
- **Edge-optimized:** Specifically designed for mobile/edge
- **Link:** https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF
- **Expected VRAM:** ~1.2GB
- **Expected Speed:** 35-45 tok/s
- **Status:** 🔥 **MUST TEST FIRST**

### 2. **Phi-3.5-Mini-Instruct** ⭐⭐⭐⭐⭐
- **Size:** 3.8B parameters (~2.3GB GGUF Q4_K_M)
- **Why:** Microsoft's edge-optimized, excellent quality, similar speed to Llama 3.2 3B
- **Tool calling:** Strong function-calling capabilities
- **Link:** https://huggingface.co/microsoft/Phi-3.5-mini-instruct
- **Expected VRAM:** ~2.3GB
- **Expected Speed:** 25-30 tok/s
- **Status:** 🔥 **HIGH PRIORITY** (quality + speed balance)

### 3. **Nemotron-Mini-4B-Instruct** ⭐⭐⭐⭐
- **Size:** 4B parameters (~2.5GB GGUF Q4_K_M)
- **Why:** NVIDIA model, optimized for function calling + edge devices
- **Tool calling:** **Explicitly optimized for this** (critical for Zoe!)
- **Link:** https://huggingface.co/nvidia/Nemotron-Mini-4B-Instruct
- **Expected VRAM:** ~2.5GB
- **Expected Speed:** 20-28 tok/s
- **Status:** 🔥 **MUST TEST** (NVIDIA + function calling)

### 4. **Qwen2.5-3B** ⭐⭐⭐⭐
- **Size:** 3B parameters (~1.8GB GGUF Q4_K_M)
- **Why:** Same Qwen family, but smaller = faster
- **Quality:** Excellent (Qwen intelligence with 3B speed)
- **Link:** https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF
- **Expected VRAM:** ~1.8GB
- **Expected Speed:** 28-35 tok/s
- **Status:** 🔥 **HIGH PRIORITY**

### 5. **Gemma-2-2B** ⭐⭐⭐
- **Size:** 2B parameters (~1.3GB GGUF Q4_K_M)
- **Why:** Google model, very small = very fast
- **Quality:** Good for 2B size
- **Link:** https://huggingface.co/google/gemma-2-2b-it
- **Expected VRAM:** ~1.3GB
- **Expected Speed:** 35-50 tok/s
- **Status:** ⚡ **ULTRA-FAST candidate**

---

## 📊 SECONDARY CANDIDATES (Worth Testing)

### 6. **SmolLM3-3B** ⭐⭐⭐
- **Size:** 3B (~1.9GB GGUF)
- **Expected Speed:** 25-30 tok/s
- **Link:** https://huggingface.co/HuggingFaceTB/SmolLM3-3B-GGUF

### 7. **StableLM-Zephyr-3B** ⭐⭐⭐
- **Size:** 3B (~1.9GB GGUF)
- **Expected Speed:** 24-28 tok/s
- **Link:** https://huggingface.co/stabilityai/stablelm-zephyr-3b

### 8. **Llama-3.2-1B** ⭐⭐
- **Size:** 1B (~650MB GGUF)
- **Expected Speed:** 50-70 tok/s (ULTRA-FAST)
- **Quality:** Lower, but worth testing for speed
- **Link:** https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct

### 9. **Qwen2-0.5B-Instruct** ⭐
- **Size:** 0.5B (~350MB GGUF)
- **Expected Speed:** 70-100 tok/s
- **Quality:** Very limited, fallback only
- **Link:** https://huggingface.co/Qwen/Qwen2-0.5B-Instruct

### 10. **DeepSeek-R1-Distill-Qwen-1.5B** ⭐⭐⭐
- **Size:** 1.5B (~1GB GGUF)
- **Expected Speed:** 40-50 tok/s
- **Quality:** Good reasoning for size
- **Link:** https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B

---

## ❌ NOT RECOMMENDED

| Model | Size | Why Skip |
|-------|------|----------|
| Mixtral-8x7B | 56B | Way too large (OOM on 16GB) |
| Qwen 7B | 7B | Already tested (9.7 tok/s - too slow) |
| BTLM-3B-8K | 3B | Research-level, may lack instruction tuning |
| Falcon-E-3B | 3B | Limited GGUF availability |
| Rocket-3B | 3B | Community model, unproven |

---

## 🎯 TESTING STRATEGY

### Phase 1: Speed Champions (< 2B models)
**Goal:** Find the FASTEST model for voice

1. **SmolLM2-1.7B** (Expected: 35-45 tok/s)
2. **Gemma-2-2B** (Expected: 35-50 tok/s)
3. **Llama-3.2-1B** (Expected: 50-70 tok/s)

**Test for:** Speed + acceptable quality + tool-calling ability

### Phase 2: Quality Balance (3-4B models)
**Goal:** Find best speed/quality trade-off

1. **Phi-3.5-Mini-3.8B** (Expected: 25-30 tok/s)
2. **Qwen2.5-3B** (Expected: 28-35 tok/s)
3. **Nemotron-Mini-4B** (Expected: 20-28 tok/s)

**Test for:** Function calling + reasoning + speed

### Phase 3: Current Baseline
**Reference:** Llama 3.2 3B (27 tok/s) ✅

---

## 📋 TESTING CHECKLIST (Per Model)

### Performance Metrics
- [ ] Generation speed (tok/s)
- [ ] Prompt processing speed (tok/s)
- [ ] VRAM usage
- [ ] 50-token response latency
- [ ] GPU utilization

### Quality Metrics
- [ ] Natural language understanding
- [ ] Tool-calling accuracy
- [ ] Response quality
- [ ] Instruction following

### Stability
- [ ] OOM errors?
- [ ] Consistent performance?
- [ ] Multi-turn conversation stability?

---

## 🚀 IMMEDIATE ACTION PLAN

### Option A: Test Top 3 Speed Champions
**Time:** ~2 hours  
**Models:**
1. SmolLM2-1.7B
2. Gemma-2-2B
3. Qwen2.5-3B

**Potential outcome:** Find 35-50 tok/s model = sub-1.5s responses!

### Option B: Test Top 2 + Baseline Comparison
**Time:** ~1 hour  
**Models:**
1. SmolLM2-1.7B (speed)
2. Phi-3.5-Mini-3.8B (quality + function calling)

**Potential outcome:** Either beat Llama 3.2 3B or confirm it's optimal

---

## 💡 EXPECTED RESULTS

### Speed Predictions on Jetson Orin NX

| Model | Size | Expected tok/s | 50-token Latency | Voice-Ready? |
|-------|------|----------------|------------------|--------------|
| Qwen2-0.5B | 0.5B | 80-100 tok/s | 0.5-0.6s | ✅ Ultra-fast (low quality) |
| Llama-3.2-1B | 1B | 50-70 tok/s | 0.7-1.0s | ✅ Very fast |
| SmolLM2-1.7B | 1.7B | 35-45 tok/s | 1.1-1.4s | ✅ **Excellent balance** |
| Gemma-2-2B | 2B | 35-50 tok/s | 1.0-1.4s | ✅ **Excellent balance** |
| **Llama-3.2-3B** | **3B** | **27 tok/s** | **1.8s** | ✅ **Current champion** |
| Qwen2.5-3B | 3B | 28-35 tok/s | 1.4-1.8s | ✅ Good |
| Phi-3.5-Mini | 3.8B | 25-30 tok/s | 1.7-2.0s | ✅ Good quality |
| Nemotron-Mini | 4B | 20-28 tok/s | 1.8-2.5s | ⚠️ Borderline |
| Qwen2.5-7B | 7B | 9.7 tok/s | 5.2s | ❌ Too slow |

---

## 🎯 RECOMMENDATION

### IMMEDIATE: Test SmolLM2-1.7B
**Why:**
- 50% smaller than current model
- Edge-optimized by Hugging Face
- Potentially 35-45 tok/s (vs current 27 tok/s)
- **Could achieve sub-1.5s responses!**

### SECONDARY: Test Phi-3.5-Mini or Nemotron-Mini-4B
**Why:**
- Strong function-calling capabilities
- Microsoft/NVIDIA backing
- May match Llama 3.2 3B with better quality

---

## ✅ NEXT STEPS

**Want me to:**
1. **Download and test SmolLM2-1.7B** (most promising for speed)
2. **Download and test Phi-3.5-Mini** (best quality + function calling)
3. **Test all Top 5** (comprehensive evaluation)
4. **Create automated testing script** for batch testing

**Which approach do you prefer?** 🚀




