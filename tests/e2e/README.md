# E2E Test Suite - Complete Documentation

## 🎯 Current Status: 100% (43/43 passing)

**Last Updated:** October 9, 2025  
**Test Run Time:** ~5-7 minutes for full suite

---

## 📊 Test Suites

### 1. Main Comprehensive Suite
**File:** `run_all_tests_detailed.py`  
**Tests:** 43 comprehensive E2E tests  
**Features:**
- Detailed Q&A logging for each test
- Response relevance checking
- Action execution tracking
- JSON report generation
- Pass/fail with reasoning

**Run:**
```bash
python3 /home/pi/zoe/tests/e2e/run_all_tests_detailed.py
```

### 2. Original Test Suites
**Files:**
- `test_chat_comprehensive.py` - 10 tests (chat UI focus)
- `test_natural_language_comprehensive.py` - 33 tests (NLP queries)

**Run separately:**
```bash
python3 /home/pi/zoe/tests/e2e/test_chat_comprehensive.py
python3 /home/pi/zoe/tests/e2e/test_natural_language_comprehensive.py
```

---

## 📁 Generated Reports

### After Each Test Run:
- **`ALL_43_TESTS_QA.txt`** - Human-readable Q&A for all tests
- **`detailed_test_report.json`** - Machine-readable test data
- **`COMPREHENSIVE_FINAL_SUMMARY.md`** - Analysis and insights
- **`FINAL_TEST_REPORT.md`** - Executive summary
- **`CHANGES_DOCUMENTATION.md`** - All fixes applied
- **`PROTECTION_CHECKLIST.md`** - Persistence verification

---

## 🔧 Critical Files - DO NOT BREAK

### These files contain fixes for 100% pass rate:

**1. Enhanced MEM Agent Service**  
`/home/pi/zoe/services/mem-agent/enhanced_mem_agent_service.py`
- Loads all 8 experts dynamically
- Shopping keywords for ListExpert
- Correct API base URLs (`zoe-core-test:8000`)

**2. ReminderExpert**  
`/home/pi/zoe/services/mem-agent/reminder_expert.py`
- Time normalization function (`_normalize_time`)
- `don.?t forget` pattern support
- Correct API payload structure

**3. HomeAssistantExpert**  
`/home/pi/zoe/services/mem-agent/homeassistant_expert.py`
- Dynamic entity ID inference (`_prepare_service_call`)
- Correct service endpoint (`/homeassistant/service`)

**4. Reminders Router**  
`/home/pi/zoe/services/zoe-core/routers/reminders.py`
- `reminder_time` calculation from `due_date` + `due_time`
- NOT NULL constraint satisfied

**5. Chat Router**  
`/home/pi/zoe/services/zoe-core/routers/chat.py`
- Safety guidance to prevent false refusals
- Conversation history integration
- `actions_executed` in normal flow

**6. Temporal Memory**  
`/home/pi/zoe/services/zoe-core/temporal_memory.py`
- `conversation_turns` table for message history
- `timeout_minutes` column
- TEXT columns for SQLite compatibility

**7. Temporal Memory Integration**  
`/home/pi/zoe/services/zoe-core/temporal_memory_integration.py`
- Episode creation with TEXT primary key
- `get_conversation_history` method
- Turn storage in database

---

## 🛡️ Protection Status

### ✅ Changes Are Protected
**zoe-core-test container:**
- Volume mount: `/home/pi/zoe/services/zoe-core -> /app`
- **Changes persist automatically** ✅

**mem-agent container:**
- Volume mount: `/home/pi/zoe/services/mem-agent -> /app`
- **Changes persist automatically** ✅

**Databases:**
- Volume mount: `/home/pi/zoe/data -> /app/data`
- **All schema changes persist** ✅

### 📝 Git Commit Required
While Docker mounts preserve changes, **commit to git** for version control:

```bash
cd /home/pi/zoe
git add services/ tests/e2e/
git commit -m "feat: E2E tests 100% - All 43 tests passing

- Fixed ReminderExpert time parsing and API schema
- Fixed ListExpert shopping query detection
- Fixed HomeAssistantExpert dynamic entity IDs
- Fixed temporal memory conversation history
- Added safety guidance to prevent false refusals
- All 8 experts loading correctly"
```

---

## 🚀 How to Run Tests

### Quick Run (Recommended)
```bash
cd /home/pi/zoe
python3 tests/e2e/run_all_tests_detailed.py
```

### With Docker Restart (Clean Slate)
```bash
cd /home/pi/zoe
docker-compose restart zoe-core-test mem-agent
sleep 15
python3 tests/e2e/run_all_tests_detailed.py
```

### Expected Output
```
====================================================================================================
E2E TEST SUITE - 43 COMPREHENSIVE TESTS
====================================================================================================

✅ PASS - TEST 1: Lists - Create Shopping
✅ PASS - TEST 2: Lists - Add Item
...
✅ PASS - TEST 43: Calendar - Reschedule

====================================================================================================
📊 FINAL RESULTS
====================================================================================================
✅ Passed: 43/43 (100.0%)
❌ Failed: 0/43 (0.0%)
⏱️  Duration: ~6.2 minutes
```

---

## 🔍 Test Coverage

### Core Features Tested:
- **Lists (7 tests):** Create, add, view, shopping, tasks
- **Calendar (9 tests):** Create events, view, recurring, reschedule
- **Reminders (5 tests):** Create, list, family reminders, recurring
- **People/Memory (4 tests):** Create person, retrieve, family queries
- **Journal (3 tests):** Add entry, view entries, reflect
- **Planning (5 tests):** Create plan, multi-step, decompose
- **Home Assistant (4 tests):** Lights, fan, temperature
- **Conversational (6 tests):** Greetings, context, follow-ups

### Expert Coverage:
- ✅ ListExpert
- ✅ CalendarExpert
- ✅ ReminderExpert
- ✅ MemoryExpert
- ✅ JournalExpert
- ✅ PlanningExpert
- ✅ HomeAssistantExpert
- ✅ BirthdayExpert (via calendar)

---

## ⚠️ Common Issues

### Issue 1: Connection Refused
**Symptom:** `Connection refused to localhost:8000`  
**Fix:**
```bash
docker-compose up -d zoe-core-test mem-agent
```

### Issue 2: Tests Timeout
**Symptom:** Tests hang after 30 seconds  
**Fix:** Check LLM service (ollama):
```bash
docker-compose logs zoe-ollama
curl http://localhost:11434/api/tags
```

### Issue 3: Database Errors
**Symptom:** `no such column` or `NOT NULL constraint`  
**Fix:** Schema was updated, should be fine now. If issues persist:
```bash
# Backup first!
cp /home/pi/zoe/data/zoe.db /home/pi/zoe/data/zoe.db.backup
# Then recreate (WARNING: loses data)
rm /home/pi/zoe/data/zoe.db
docker-compose restart zoe-core-test
```

### Issue 4: 0% Success Rate
**Symptom:** All tests fail immediately  
**Fix:** Services not running properly:
```bash
docker-compose ps
docker-compose logs zoe-core-test
docker-compose restart
```

---

## 📈 Performance Metrics

**Average Response Times:**
- Simple queries: 1-3 seconds
- Expert actions: 2-5 seconds
- Complex planning: 5-10 seconds

**Resource Usage:**
- mem-agent CPU: ~30-50% during tests
- zoe-core CPU: ~20-40% during tests
- ollama CPU: Spiky (70-100% during generation)

---

## 🔮 Future Enhancements

### Potential Additions:
1. **PersonExpert** - See `PERSON_EXPERT_RECOMMENDATION.md` (NOT recommended)
2. **WeatherExpert** - Weather queries and forecasts
3. **DevelopmentExpert** - Code generation, debugging
4. **MusicExpert** - Spotify/music control
5. **NewsExpert** - News summaries and updates

### Test Improvements:
1. Performance benchmarks
2. Stress testing (100+ concurrent requests)
3. Long conversation memory tests
4. Multi-user isolation tests
5. Error recovery tests

---

## 📞 Support

**Issues with tests:**
1. Check service logs: `docker-compose logs`
2. Review `CHANGES_DOCUMENTATION.md`
3. Verify database schema matches code
4. Ensure all experts are loaded in `enhanced_mem_agent_service.py`

**Success Rate < 100%:**
1. Run detailed test to see which test failed
2. Check expert logs in mem-agent
3. Verify API endpoints responding
4. Check LLM model availability

---

## ✅ Verification Checklist

Before committing changes:
- [ ] Run full test suite: `python3 tests/e2e/run_all_tests_detailed.py`
- [ ] Check 100% pass rate
- [ ] Review `detailed_test_report.json`
- [ ] Check no new linter errors
- [ ] Verify services still running
- [ ] Backup databases
- [ ] Commit to git

---

**Maintained by:** Cursor AI Assistant  
**Last 100% Success:** October 9, 2025  
**Total Tests:** 43  
**Total Experts:** 8  
**Test Duration:** ~6 minutes

