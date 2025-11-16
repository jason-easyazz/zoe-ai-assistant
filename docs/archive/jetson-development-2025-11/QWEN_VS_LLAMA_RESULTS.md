# 🎯 Qwen 2.5 7B vs Llama 3.2 3B: ACTUAL BENCHMARK RESULTS

**Date:** 2025-11-13  
**Hardware:** Jetson Orin NX 16GB (MAXN_SUPER mode, clocks maximized)

---

## 📊 PERFORMANCE COMPARISON

### Llama 3.2 3B (Q4_K_M) - Previously Tested
- **Generation Speed:** 27.17 tok/s ✅
- **Prompt Processing:** 48.02 tok/s
- **Model Size:** 1.9GB VRAM
- **Response Time (50 tokens):** ~1.8 seconds
- **GPU Offload:** 29/29 layers (100%)
- **Status:** FAST & RESPONSIVE for voice ✅

### Qwen 2.5 7B (Q3_K_M) - Current Test
- **Generation Speed:** 9.7 tok/s ⚠️ **SLOW**
- **Prompt Processing:** 29.6 tok/s
- **Model Size:** 3.4GB VRAM
- **Response Time (50 tokens):** ~5.2 seconds
- **GPU Offload:** 29/29 layers (100%)
- **Status:** **TOO SLOW for real-time voice** ❌

---

## 🚨 CRITICAL FINDINGS

### Speed Deficit
| Metric | Llama 3.2 3B | Qwen 2.5 7B | Difference |
|--------|-------------|-------------|------------|
| **Tokens/sec** | 27.2 | 9.7 | **-64% (2.8x slower)** |
| **50 token response** | 1.8s | 5.2s | **+189% latency** |
| **100 token response** | 3.7s | 10.3s | **+178% latency** |

### Why So Slow?
**Expected:** 23.5 tok/s (per NVIDIA benchmarks)  
**Actual:** 9.7 tok/s  
**Gap:** 59% slower than expected!

**Possible reasons:**
1. **More parameters = slower inference** (7B vs 3B)
2. **Q3 quantization** may be slower than Q4 on this hardware
3. **Memory bandwidth bottleneck** (3.4GB vs 1.9GB)
4. **Different llama.cpp version** than NVIDIA's tests
5. **Competing processes** consuming resources
6. **Unified memory architecture** limiting throughput

---

## 🎙️ REAL-TIME VOICE IMPACT

### Voice Conversation Requirements
- **Target latency:** < 2 seconds for natural conversation
- **Average response:** 30-50 tokens (one sentence)

### Latency Analysis

**Llama 3.2 3B (27 tok/s):**
```
30 tokens = 1.1s ✅ Excellent
50 tokens = 1.8s ✅ Great
100 tokens = 3.7s ✅ Acceptable
```

**Qwen 2.5 7B (9.7 tok/s):**
```
30 tokens = 3.1s ⚠️ Noticeable lag
50 tokens = 5.2s ❌ Too slow
100 tokens = 10.3s ❌ Unusable
```

---

## 🤔 INTELLIGENCE vs SPEED TRADE-OFF

### What You Get with Qwen 2.5 7B
✅ **Better reasoning** (7B parameters vs 3B)  
✅ **Better code generation**  
✅ **Better instruction following**  
✅ **Better tool calling (maybe)**  
✅ **More context understanding**

### What You Lose
❌ **2.8x SLOWER generation** (9.7 vs 27 tok/s)  
❌ **Real-time voice becomes laggy** (5s responses)  
❌ **User experience degrades significantly**  
❌ **70% more VRAM** (3.4GB vs 1.9GB)  
❌ **Less headroom for other services** (Whisper, TTS)

---

## 🎯 RECOMMENDATION

### For Real-Time Voice: **KEEP LLAMA 3.2 3B**

**Reasons:**
1. **Speed is CRITICAL for voice** - 1.8s feels natural, 5s feels broken
2. **Llama 3.2 3B is "smart enough"** - handles tool calling, natural language, memory
3. **More VRAM headroom** - can run Whisper + TTS simultaneously
4. **Proven stable** - no OOM errors, consistent performance
5. **Users value responsiveness over intelligence** in voice interfaces

### For Text/Complex Tasks: Consider Qwen via API

If you need Qwen-level intelligence for specific tasks:
- Use **external API** (OpenRouter, Groq, etc.) for complex reasoning
- Keep **Llama 3.2 3B on-device** for fast, real-time voice
- **Hybrid approach:** Fast local LLM + smart cloud LLM when needed

---

## 📈 PERFORMANCE SUMMARY

```
┌─────────────────┬──────────────┬─────────────┬──────────────┐
│ Model           │ Speed        │ Intelligence│ Voice Ready  │
├─────────────────┼──────────────┼─────────────┼──────────────┤
│ Llama 3.2 3B    │ 27 tok/s ✅  │ Good        │ ✅ YES       │
│ Qwen 2.5 7B Q3  │ 9.7 tok/s ⚠️│ Excellent   │ ❌ TOO SLOW  │
└─────────────────┴──────────────┴─────────────┴──────────────┘
```

---

## ✅ FINAL DECISION

**Switch BACK to Llama 3.2 3B for production voice use.**

Qwen 2.5 7B is impressive, but **speed matters more than intelligence** for real-time voice conversations. The 2.8x slower performance makes it unsuitable for your use case.

---

## 🔄 ACTION ITEMS

1. ✅ Benchmark complete
2. ⏭️ Switch back to Llama 3.2 3B
3. ⏭️ Update docker-compose.yml
4. ⏭️ Test voice latency end-to-end
5. ⏭️ Archive Qwen as "tested but too slow"

**Status:** Analysis complete, ready to revert to Llama 3.2 3B.





