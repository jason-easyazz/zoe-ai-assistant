# Jetson Orin NX Upgrade Checklist

**Hardware**: ReComputer Super J4012 (NVIDIA Jetson Orin NX 16GB)  
**Target Model**: Gemma 3 4B  
**Goal**: Real-time, human-like conversation with zero lag voice interaction

---

## Pre-Upgrade Testing (Complete Before Hardware Arrives)

### Software Verification
- [ ] Run automated test suite: `pytest tests/integration/test_human_like_conversation.py -v`
- [ ] Verify all 20+ test scenarios pass
- [ ] Test manual conversation scenarios from `natural_conversation_manual_tests.md`
- [ ] Confirm temporal memory works: "What did I just tell you?"
- [ ] Confirm orchestration triggers: "Add milk AND create event tomorrow"
- [ ] Verify no timeouts: All queries respond in <10s
- [ ] Check satisfaction tracking populates database

### Database Optimization
- [ ] Run: `python3 /home/zoe/assistant/scripts/utilities/optimize_database_indexes.py`
- [ ] Verify memory search <1s: Check EXPLAIN QUERY PLAN output
- [ ] Confirm indexes created on all key tables
- [ ] Test complex queries don't timeout

### Current Performance Baseline (Pi 5)
- [ ] Document average response time: _____ seconds
- [ ] Document memory search time: _____ seconds  
- [ ] Document test pass rate: _____% (target: 80%+)
- [ ] Record any failing scenarios for Jetson re-test

---

## Hardware Installation

### Physical Setup
1. [ ] **Backup current Pi 5 SD card** (crucial!)
   ```bash
   sudo dd if=/dev/mmcblk0 of=/backup/pi5-zoe-backup.img bs=4M status=progress
   ```

2. [ ] **Prepare Jetson Orin NX**
   - Flash JetPack 6.0 (or latest)
   - Set up networking
   - Configure hostname: `zoe-jetson`

3. [ ] **Transfer Zoe to Jetson**
   ```bash
   # On Pi 5
   rsync -avz --progress /home/zoe/assistant/ zoe-jetson:/home/nvidia/zoe/
   
   # Or use external SSD/USB
   ```

4. [ ] **Install dependencies on Jetson**
   ```bash
   cd /home/nvidia/zoe
   
   # Docker
   sudo apt install docker.io docker-compose
   
   # Python dependencies
   pip3 install -r requirements.txt
   
   # Ollama (ARM64 for Jetson)
   curl https://ollama.ai/install.sh | sh
   ```

---

## Model Migration

### Install Gemma 3 4B
```bash
# On Jetson
ollama pull gemma3:4b

# Verify model
ollama list
# Should show: gemma3:4b    4.1GB

# Test model
ollama run gemma3:4b "Hello, test"
```

### Update Zoe Configuration

**File**: `/home/nvidia/zoe/services/zoe-core/model_config.py`

```python
# Update model selection
AVAILABLE_MODELS = {
    "gemma3:4b": {
        "category": ModelCategory.FAST,
        "timeout": 10.0,  # Faster on Jetson
        "temperature": 0.7,
        "num_predict": 500,
        "num_ctx": 4096,  # Increased context
    }
}

# Update primary models
FAST_MODEL = "gemma3:4b"
CONVERSATION_MODEL = "gemma3:4b"
```

**File**: `/home/nvidia/zoe/services/zoe-core/routers/chat.py`

```python
# Line ~461 - Update default models
if routing_type == "action":
    model = "gemma3:4b"  # Better for tool calling
elif routing_type == "conversation":
    model = "gemma3:4b"  # Better for conversations
```

---

## Performance Benchmarking

### Response Time Tests
```bash
# Test 1: Simple query
time curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "user_id": "perf_test"}'

# Target: <2s (vs Pi 5: ~6-14s)

# Test 2: Complex memory query
time curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What events do I have this week?", "user_id": "perf_test"}'

# Target: <3s (vs Pi 5: ~10-30s)

# Test 3: Orchestration
time curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Plan my day and add coffee to shopping list", "user_id": "perf_test"}'

# Target: <5s (vs Pi 5: timeout)
```

### Voice Performance Tests
1. [ ] Test voice input latency: STT processing time
2. [ ] Test LLM response time: Model inference
3. [ ] Test TTS output latency: Speech generation
4. [ ] **Total round-trip target**: <3s

### GPU Utilization
```bash
# Monitor GPU during queries
sudo tegrastats

# Expected: 40-60% GPU usage during inference
# Expected: <8GB memory usage
```

---

## Verification Checklist

### Functional Tests (Must Pass 100%)
- [ ] Temporal memory recall works: "What did I just say?"
- [ ] Orchestration triggers: Multi-step tasks execute
- [ ] No timeouts: All queries <10s (target: <3s)
- [ ] Satisfaction tracking: Data populates database
- [ ] Episode context: Appears in prompts
- [ ] Memory search: <1s response time
- [ ] Voice pipeline: Zero perceived lag

### Performance Targets (Jetson)
| Metric | Pi 5 Baseline | Jetson Target | Actual |
|--------|---------------|---------------|--------|
| Simple query | 6-14s | <2s | ___ s |
| Complex query | 10-30s | <3s | ___ s |
| Orchestration | Timeout | <5s | ___ s |
| Voice round-trip | N/A | <3s | ___ s |
| Memory search | ~0.3s | <0.1s | ___ s |
| Test pass rate | 80% | 95%+ | ___% |

### Conversation Quality
- [ ] Run all 10 manual test scenarios
- [ ] Test 10-turn conversation flows
- [ ] Verify pronoun resolution works
- [ ] Confirm temporal references work
- [ ] Test conversational repairs ("wait, I meant...")

---

## Rollback Procedure (If Issues Occur)

### Quick Rollback to Pi 5
1. **Stop Jetson**
   ```bash
   cd /home/nvidia/zoe
   ./stop-zoe.sh
   ```

2. **Restore Pi 5**
   ```bash
   # Restore from backup
   sudo dd if=/backup/pi5-zoe-backup.img of=/dev/mmcblk0 bs=4M status=progress
   
   # Or switch back to Pi 5 hardware
   ```

3. **Revert code changes** (if model-specific)
   ```bash
   git checkout HEAD~1 services/zoe-core/model_config.py
   git checkout HEAD~1 services/zoe-core/routers/chat.py
   ```

### Partial Rollback (Keep Jetson, Use Old Model)
```bash
# Install llama3.2:3b on Jetson
ollama pull llama3.2:3b

# Revert model config to use llama3.2:3b
# (Keep all other improvements)
```

---

## Post-Upgrade Optimization

### Fine-Tuning for Best Performance
1. [ ] **Adjust model parameters** based on response quality
   - Temperature (0.6-0.9)
   - Top-p (0.8-0.95)
   - Context window (2048-8192)

2. [ ] **Monitor GPU memory usage**
   ```bash
   # If memory issues, quantize model
   ollama pull gemma3:4b-q4_0  # 4-bit quantization
   ```

3. [ ] **Enable Jetson power modes**
   ```bash
   # Max performance (157 TOPS)
   sudo nvpmodel -m 0
   sudo jetson_clocks
   ```

4. [ ] **Test under load**
   - Concurrent requests
   - Voice streaming
   - Multi-user scenarios

---

## Success Criteria

### Must Achieve (Go/No-Go)
- ✅ All automated tests pass (95%+)
- ✅ Response time <3s average
- ✅ Voice interaction feels real-time
- ✅ No regression in features
- ✅ Temporal memory works flawlessly

### Nice to Have
- Response time <2s average
- GPU utilization optimized (50-70%)
- Voice round-trip <2s
- Test pass rate 100%

---

## Timeline

**Week 1: Pre-Upgrade**
- Day 1-2: Complete software fixes (Phases 1-6)
- Day 3-4: Run all tests, document baseline
- Day 5-7: Optimize database, verify readiness

**Week 2: Hardware Upgrade**
- Day 1: Jetson setup, Zoe transfer
- Day 2: Ollama + Gemma 3 installation
- Day 3: Configuration updates, testing
- Day 4: Performance benchmarking
- Day 5: Fine-tuning and optimization
- Day 6-7: Final verification, go-live

**Week 3: Monitoring**
- Monitor performance
- Collect user feedback
- Fine-tune parameters
- Document results

---

## Support Information

### If Issues Arise

**Hardware Issues**:
- Jetson power/thermal problems → Check cooling, power supply
- GPU errors → Verify JetPack version, drivers

**Software Issues**:
- Model won't load → Check ARM64 compatibility, Ollama version
- Slow performance → Verify power mode, check GPU usage
- Timeouts return → Check model size vs memory

**Rollback Decision Points**:
1. If tests fail >50% → Rollback immediately
2. If response time >10s → Investigate before rollback
3. If voice lag noticeable → Check each pipeline stage
4. If features broken → Rollback, fix, redeploy

### Testing Commands
```bash
# Full test suite
cd /home/nvidia/zoe
pytest tests/integration/test_human_like_conversation.py -v

# Quick smoke test
curl -X POST http://localhost:8000/api/chat \
  -d '{"message": "Test", "user_id": "test"}' | jq '.response_time'

# Database check
python3 scripts/utilities/optimize_database_indexes.py

# Service health
./verify_updates.sh
```

---

## Documentation

**After successful upgrade, update:**
- [ ] `/home/nvidia/zoe/README.md` - Update hardware specs
- [ ] `/home/nvidia/zoe/PROJECT_STATUS.md` - Update performance metrics
- [ ] `/home/nvidia/zoe/CHANGELOG.md` - Add v2.4 "Jetson Upgrade" entry
- [ ] `/home/nvidia/zoe/docs/guides/pre-jetson-implementation-tracking.md` - Mark complete

---

**Prepared**: October 13, 2025  
**Ready for**: Jetson hardware arrival  
**Expected Outcome**: Real-time, human-like Zoe conversations with zero lag! 🚀



