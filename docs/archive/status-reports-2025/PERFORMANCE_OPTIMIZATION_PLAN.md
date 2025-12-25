# Performance Optimization Plan

## 🔥 Current Issues

### Observed Performance:
- ✅ Simple queries: 0.4-0.8s (GOOD)
- ⚠️ Intent queries: 0.01s but returning "N/A" (needs fix)
- ❌ Complex queries: 21s (TOO SLOW)

### Root Causes:
1. **Token Generation:** 512 tokens at 40ms/token = 20.8s
2. **No Streaming:** User waits for full response
3. **Fixed Token Limits:** Same limit for all queries
4. **Slow Token Rate:** 25 tokens/sec (should be 50-100+)
5. **No Caching:** Repeated queries regenerate

---

## 🎯 Quick Wins (Implement Now)

### 1. Reduce Default Token Limit ⚡
**Impact:** -50% response time for most queries  
**Effort:** 5 minutes

```python
# Change from 512 → 256 for normal queries
"num_predict": 256  # Most answers fit in 256 tokens
```

**Why:** 
- Most queries don't need 512 tokens
- 256 tokens = ~200 words (plenty for most answers)
- Long answers can still generate, will just be more concise

### 2. Adaptive Token Limits 🎯
**Impact:** Right-sized responses  
**Effort:** 15 minutes

```python
QUERY_TOKEN_LIMITS = {
    "greeting": 50,       # "Hello!" = 5 tokens
    "simple": 128,        # Quick facts
    "normal": 256,        # Default
    "complex": 512,       # Detailed explanations
    "detailed": 1024      # Very detailed (rarely)
}
```

### 3. Enable Streaming 🌊
**Impact:** Perceived latency -90%  
**Effort:** Already implemented, just use it!

Users see first token in <1s, don't wait for 20s

### 4. Response Caching 💾
**Impact:** -99% for repeated queries  
**Effort:** 20 minutes

Cache responses for 5 minutes for exact queries

---

## 🚀 Medium Impact (Implement Next)

### 5. GPU Optimization 🎮
**Current:** Slow token generation (25 tokens/sec)  
**Target:** 50-100 tokens/sec on Jetson

**Check:**
- GPU layers properly set
- Model fully loaded on GPU
- No CPU bottlenecks

### 6. Model Size Optimization 📉
**Current:** Using llama3.2:3b for everything  
**Better:** Route by complexity

```python
"hello" → llama3.2:1b (faster, good enough)
"complex query" → qwen2.5:7b (better quality)
```

### 7. Context Optimization 📚
**Current:** Fetching full context every time  
**Better:** Smart context filtering (P0-1 already helps!)

---

## 💡 Advanced Optimizations (Later)

### 8. Prompt Caching
Cache system prompts (saves ~200 tokens processing)

### 9. Batch Processing
Process multiple queries in parallel when possible

### 10. Response Templates
Pre-cache common response patterns

---

## 📊 Expected Improvements

| Optimization | Latency Reduction | Effort |
|--------------|-------------------|--------|
| Token Limit Reduction | -50% | 5 min |
| Adaptive Limits | -30% | 15 min |
| Enable Streaming | -90% perceived | 0 min (already done) |
| Response Caching | -99% (cache hits) | 20 min |
| GPU Optimization | -40% | 30 min |
| Model Routing | -30% (simple queries) | 20 min |

**Combined Impact:**
- Simple queries: 0.4s → 0.2s
- Normal queries: 5s → 2s
- Complex queries: 21s → 5s (with streaming, feels like 1s)

---

## 🔧 Implementation Order

### Phase 1: Quick Fixes (30 minutes)
1. ✅ Reduce default token limit: 512 → 256
2. ✅ Add adaptive token limits
3. ✅ Ensure streaming is default
4. ✅ Add response caching

**Expected:** -60% latency

### Phase 2: GPU Optimization (1 hour)
5. ✅ Check GPU utilization
6. ✅ Optimize model loading
7. ✅ Add model routing by complexity

**Expected:** -70% total latency

### Phase 3: Advanced (2 hours)
8. ✅ Prompt caching
9. ✅ Batch processing
10. ✅ Response templates

**Expected:** -80% total latency

---

## 🧪 Testing Strategy

### Before Each Change:
```bash
# Measure baseline
time curl POST /api/chat "query"

# Make change
# ...

# Measure after
time curl POST /api/chat "query"

# Compare
```

### Test Queries:
1. "hello" (should be <0.5s)
2. "what time is it?" (should be <1s)
3. "tell me about quantum physics" (should be <5s with streaming)

---

## 🎯 Target Performance

| Query Type | Current | Target | Method |
|------------|---------|--------|--------|
| Greeting | 0.8s | 0.2s | Token limit, cache |
| Intent | 0.4s | 0.1s | Skip LLM |
| Simple | 5s | 1s | Token limit, streaming |
| Complex | 21s | 5s | Token limit, streaming, GPU |

---

## 🚀 Let's Start

Implementing Phase 1 now...

