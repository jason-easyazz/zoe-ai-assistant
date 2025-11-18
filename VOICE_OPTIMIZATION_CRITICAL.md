# 🎙️ CRITICAL: Voice Optimization Needed

## Current Status: NOT OPTIMIZED FOR VOICE

**Voice Requirements:**
- First token: <500ms ✅ (we're at ~300ms)
- Total response: <2s ❌ (we're at 3-5s)
- Streaming: Required ✅ (available)
- Concise responses: Required ❌ (currently 184-256 tokens)

**Current bottleneck:** 
- 3B model is too slow for real-time voice
- Generating 184 tokens at 67 t/s = 2.7s (acceptable but slow)
- Need faster responses for natural conversation

---

## 🚨 Critical Voice Optimizations

### 1. AGGRESSIVE Token Limit ⚡⚡⚡
**Impact:** 3-5s → 1-2s (50% faster)

**Current:** max_tokens=256, generating ~184 tokens  
**Voice-optimized:** max_tokens=128 (force concise responses)

Voice responses should be:
- Short and conversational
- 50-100 tokens max
- Not essay-length explanations

```yaml
# In route_llm.py or chat endpoint
"num_predict": 128  # Instead of 256
```

**Effect:**
- 128 tokens at 67 t/s = 1.9s ✅
- Forces model to be concise
- Better for voice anyway

### 2. Reduce Context Further ⚡⚡
**Impact:** 15-20% faster prompt processing

**Current:** CTX_SIZE=1024  
**Voice-optimized:** CTX_SIZE=512

Voice conversations don't need long context:
- Typical conversation: 3-4 turns
- ~100 tokens per turn = 400 tokens max
- 512 is plenty

### 3. Use Smaller Model for Simple Queries ⚡⚡⚡
**Impact:** 3x faster (3s → 1s)

**Load llama3.2:1b for:**
- Greetings ("hello", "hi")
- Simple commands ("set timer", "play music")
- Quick questions ("what's the time?")

**Keep 3B for:**
- Complex questions
- Detailed explanations
- Multi-step tasks

**Expected:**
- Simple queries: 1s → 0.3s
- Complex queries: 3s → 1.5s (with token limit)

### 4. Voice-Specific Prompt ⚡
**Impact:** More concise responses

Add to system prompt:
```
VOICE MODE: Keep responses under 50 words. Be conversational and brief.
```

---

## 🎯 Voice-Optimized Configuration

```yaml
# docker-compose.yml - Voice profile
zoe-llamacpp:
  environment:
    - CTX_SIZE=512          # Voice: short context
    - N_GPU_LAYERS=33       # Keep GPU working
    - N_BATCH=128           # Smaller batch for voice
    - N_UBATCH=64           # Smaller micro-batch
    - THREADS=8
    - PARALLEL=4            # Reduce parallel for lower latency
```

```python
# route_llm.py - Voice profile
"num_predict": 128,  # Force concise voice responses
"temperature": 0.7,  # Keep natural
```

---

## 📊 Expected Voice Performance

### Current (Chat-optimized):
- Greeting: 0.6s
- Simple: 1-2s  
- Complex: 3-5s
- **Voice UX: Acceptable but slow**

### After Voice Optimization:
- Greeting: 0.3s ✅
- Simple: 0.8-1.2s ✅
- Complex: 1.5-2s ✅
- **Voice UX: Natural conversation**

### With 1B Model for Simple:
- Greeting: 0.2s ✅✅✅
- Simple: 0.4-0.6s ✅✅✅
- Complex: 1.5-2s ✅
- **Voice UX: Instant responses**

---

## 🚀 Implementation Steps

### Immediate (5 min):
1. Reduce token limit to 128
2. Reduce context to 512
3. Reduce parallel to 4

**Result:** 40% faster for voice

### Short-term (30 min):
4. Add voice-specific prompt
5. Implement query complexity detector
6. Route simple queries to faster path

**Result:** 60% faster for voice

### Medium-term (2 hours):
7. Load llama3.2:1b alongside 3B
8. Smart routing between models
9. Voice-optimized prompts per model

**Result:** 70% faster for voice

---

## ⚠️ Trade-offs

### Shorter token limits:
- **Pro:** Much faster responses
- **Pro:** More concise (better for voice!)
- **Con:** Can't give detailed explanations
- **Solution:** Let user ask follow-up if they want more

### Smaller context:
- **Pro:** Faster prompt processing
- **Con:** Forgets older conversation
- **Solution:** 512 tokens = 4-5 turns (enough for voice)

### Dual models (1B + 3B):
- **Pro:** Best of both worlds
- **Con:** Uses ~2GB GPU memory total
- **Solution:** Jetson can handle it

---

## 🎙️ Voice-Specific Best Practices

1. **Always stream** - User hears response starting immediately
2. **Short responses** - 50-100 tokens ideal for voice
3. **Interrupt handling** - Must support stopping mid-response
4. **No markdown** - Voice doesn't need formatting
5. **Conversational** - Not essay-style

---

## 📋 Recommended Action

**For production voice:**

Apply aggressive optimizations NOW:
```bash
# 1. Edit docker-compose.yml
CTX_SIZE=512
N_BATCH=128
N_UBATCH=64
PARALLEL=4

# 2. Edit route_llm.py
num_predict: 128

# 3. Restart
docker compose stop zoe-llamacpp && docker compose rm -f zoe-llamacpp && docker compose up -d zoe-llamacpp
```

**Expected result:**
- Voice responses: 1-2s (down from 3-5s)
- Natural conversation pace
- Still good quality
- GPU fully utilized

This is **critical** for real-time voice to feel natural!

