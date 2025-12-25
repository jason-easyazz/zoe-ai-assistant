# Performance Optimization Opportunities

## Current Performance
- Complex queries: 3.8s (67 tokens/sec on GPU)
- Simple queries: 0.8s
- Token generation: Working well with GPU

## 🚀 High-Impact Optimizations

### 1. Enable Streaming by Default ⚡⚡⚡
**Impact:** 95% perceived latency reduction  
**Effort:** 5 minutes

**Current:** User waits 3.8s for full response  
**With streaming:** User sees first words in 0.3s

```python
# In chat.py, default stream=True
stream = msg.stream if hasattr(msg, 'stream') else True  # Changed from False
```

**Why this matters:**
- User sees response starting immediately
- Feels instant even for long responses
- Much better UX

### 2. Reduce Context Window ⚡⚡
**Impact:** 20-30% faster prompt processing  
**Effort:** 2 minutes

**Current:** CTX_SIZE=2048 (overkill for most queries)  
**Optimized:** CTX_SIZE=1024 (plenty for most conversations)

```yaml
- CTX_SIZE=1024  # Reduced from 2048
```

**Tradeoff:** Long conversations might need trimming, but most fit easily

### 3. Query-Based Model Routing ⚡⚡
**Impact:** 3x faster for simple queries  
**Effort:** 30 minutes

**Idea:** Route by complexity
- Simple queries (greetings, commands) → llama3.2:1b (much faster)
- Complex queries (explanations) → llama3.2:3b (current)

**Expected:**
- "hello" → 0.2s (vs 0.8s now)
- "turn on lights" → 0.1s (via intent, skip LLM)
- "explain quantum physics" → 3.8s (unchanged)

### 4. Prompt Caching ⚡
**Impact:** 30% faster for repeated system prompts  
**Effort:** Built-in to llama.cpp

**Current:** System prompt processed every query (~250ms)  
**With caching:** System prompt cached (~50ms)

Already enabled with `tokens_cached` in logs!

### 5. Reduce Batch Sizes (Minimal gain) ⚡
**Impact:** 5-10% faster  
**Effort:** 2 minutes

**Current:** N_BATCH=512, N_UBATCH=256  
**Optimized:** N_BATCH=256, N_UBATCH=128

Less memory, slightly faster for short responses.

---

## 📊 Expected Improvements

| Optimization | Simple Queries | Complex Queries | Effort |
|--------------|----------------|-----------------|--------|
| Enable Streaming | 0.8s → 0.3s perceived | 3.8s → 0.3s perceived | 5 min |
| Reduce Context | 0.8s → 0.6s | 3.8s → 3.0s | 2 min |
| Model Routing | 0.8s → 0.2s | 3.8s (same) | 30 min |
| Combined | **0.8s → 0.2s perceived** | **3.8s → 0.3s perceived** | 37 min |

---

## 🎯 Recommended Implementation Order

### Phase 1: Instant UX (7 minutes) ⚡⚡⚡
1. Enable streaming by default
2. Reduce context window to 1024

**Result:** Queries feel instant, 30% actually faster

### Phase 2: Smart Routing (30 minutes) ⚡⚡
3. Implement model routing by complexity
4. Reduce batch sizes

**Result:** Simple queries 3-4x faster

### Phase 3: Advanced (optional, later)
5. Implement response caching for common queries
6. Add query complexity predictor
7. Parallel model loading (1B + 3B in memory)

---

## ⚠️ Trade-offs

### Streaming
- **Pro:** Feels instant, better UX
- **Con:** More complex error handling
- **Verdict:** DO IT - huge win

### Reduce Context
- **Pro:** Faster, less memory
- **Con:** Very long conversations might truncate
- **Verdict:** DO IT - 1024 is plenty

### Model Routing
- **Pro:** Much faster for simple queries
- **Con:** Need to load 2 models (uses more memory)
- **Verdict:** OPTIONAL - good for optimization

---

## 🧪 Testing Plan

```bash
# Test 1: Streaming
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "explain quantum physics", "user_id": "test", "stream": true}'
# Should see data chunks immediately

# Test 2: Reduced context
# Measure before/after with same query

# Test 3: Model routing
# Simple queries should route to 1B model
```

---

## 💡 Quick Win Implementation

Want to implement Phase 1 now? (7 minutes)
- Enable streaming by default
- Reduce context to 1024
- Immediate 30% improvement + feels instant

