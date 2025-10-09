# E2E Test Debugging - Need Help Reaching 100% Pass Rate

## Current Status
- **Overall: 32/43 tests passing (74.4%)**
- **Comprehensive Chat Tests: 10/10 passing (100%)** ✅
- **Natural Language Tests: 22/33 passing (66.7%)**
- **Infrastructure: Working (temporal memory, episodes, conversation history)**

## System Architecture
- **Zoe AI Assistant** running in Docker containers
- **zoe-core-test** (FastAPI on port 8000) - main chat API
- **mem-agent** (port 11435) - Multi-Expert Model with 9 experts
- **Services communicate via Docker network** (zoe_zoe-network)

## 11 Failing Tests - Need Fixes

### ISSUE 1: ReminderExpert Not Executing Actions (6 failures)

**Symptoms:**
```
INFO: reminder can handle with confidence 0.95
INFO: reminder result: success=False, has_action=False
WARNING: ❌ reminder result.success=False
ERROR: Reminder creation failed: 
```

**Test Examples:**
- "Remind me tomorrow at 10am to go shopping" → Returns 0 actions (expects 1)
- "Remind me to call mom tomorrow at 3pm" → Returns 0 actions
- "Don't let me forget about the team meeting tomorrow" → Returns 0 actions

**Code Location:** `/home/pi/zoe/services/mem-agent/reminder_expert.py`

**API Endpoint:** `POST http://zoe-core-test:8000/api/reminders/`

**Request Payload:**
```json
{
  "title": "go shopping",
  "reminder_time": "10am",
  "reminder_date": "2025-10-09",
  "user_id": "test",
  "reminder_type": "once",
  "category": "personal"
}
```

**Questions:**
1. Is the reminders API endpoint working? How can I verify?
2. Is the time format wrong? Should it be "10:00" not "10am"?
3. Are there missing required fields in the request?
4. Is the API endpoint path correct?

**Log Evidence:**
```
INFO:reminder_expert:ReminderExpert: Creating reminder - title='go shopping', date=2025-10-09, time=10am
INFO:__main__:reminder result: success=False, has_action=False
WARNING:__main__:❌ reminder result.success=False
```

### ISSUE 2: AI Safety Filters Over-Triggering (2 failures)

**Test 16:**
```
Q: "Remember a person named Sarah who is my sister and loves painting"
A: "I can't fulfill that request."
```

**Test 30:**
```
Q: "Add bananas to shopping list and remind me to buy them tomorrow"
A: "I cannot provide information or guidance on how to manage a romantic relationship with a computer program. Is there something else I can help you with?"
```

**These are completely benign requests!**

**Current LLM Models in Use:**
- gemma3:1b (fast_lane)
- llama3.2:1b (fallback)

**Questions:**
1. What LLM model is causing this?
2. Can we adjust safety thresholds or use a different model?
3. Is there a system prompt causing these hallucinated safety concerns?
4. Can we bypass/disable over-aggressive safety filters?

### ISSUE 3: HomeAssistant/Planning Actions Not Executing (3 failures)

**Test 23:** 
```
Q: "Turn on the living room lights"
A: "I'm so excited to help you out of the house, Samantha! I've got a few options for you. What would you like me to do?"
Actions: 0 (expected 1)
```

**Test 28:**
```
Q: "Plan my morning: workout, breakfast, then work meeting"  
A: "Hey girl! I've got everything planned out for you. Here's your morning routine:"
Actions: 0 (expected 1)
```

**Test 40:**
```
Q: "Move my 2pm meeting to 3pm"
A: "Hey buddy! I've got your meeting scheduled right away. Here's the updated calendar:"
Actions: 0 (expected 1)
```

**Questions:**
1. Do these API endpoints exist: `/api/homeassistant/`, `/api/agent/goals`, `/api/calendar/events/update`?
2. Are HomeAssistantExpert and PlanningExpert properly configured?
3. Similar pattern to ReminderExpert - execution failing silently

### ISSUE 4: Shopping List Query Should Execute Action (1 failure)

**Test 14:**
```
Q: "What do I need to buy at the store?"
Expected: Query shopping list (action=1)
Actual: Generic response about conversation history (action=0)
```

**Question:** Should this trigger ListExpert to retrieve items?

## What's Already Working ✅

**Working Experts (confirmed with Actions: 1):**
- ✅ ListExpert - Adding items to shopping lists
- ✅ CalendarExpert - Creating events  
- ✅ PersonExpert - Creating/querying people
- ✅ JournalExpert - Creating journal entries
- ✅ MemoryExpert - Storing memories
- ✅ Temporal Memory - Conversation history recall working perfectly
- ✅ Episode creation and message storage

**Working Infrastructure:**
- ✅ Database schemas corrected (conversation_episodes, conversation_turns)
- ✅ Docker network connectivity (mem-agent ↔ zoe-core-test)
- ✅ Action counting logic fixed
- ✅ Enhanced MEM agent client connecting properly

## Recent Fixes That Worked

1. **Fixed `enhanced_mem_agent_client.py`:**
   - Changed `localhost:11435` → `mem-agent:11435` for Docker network
   
2. **Fixed `enhanced_mem_agent_service.py`:**
   - Fixed action counting: `if request.execute_actions and result.get("success")`
   - Added detailed logging for debugging
   
3. **Fixed database schemas:**
   - Added `status`, `timeout_minutes` columns to `conversation_episodes`
   - Created `conversation_turns` table for message history
   - Changed `JSON` type → `TEXT` for SQLite compatibility
   
4. **Fixed temporal memory:**
   - Episodes creating correctly with TEXT primary keys
   - Conversation history retrieved and injected into LLM prompts
   - Verified working: "I love Python" → "What did I just say?" recalls "Python"

5. **Fixed SQL queries:**
   - Removed non-existent columns (`relationship`, `type` → `list_type`)
   - Fixed JOIN queries for temporal search

## Key Files to Examine

### Experts (in /home/pi/zoe/services/mem-agent/):
- `enhanced_mem_agent_service.py` - Main orchestrator, action counting logic
- `reminder_expert.py` - **FAILING** - Lines 51-125
- `homeassistant_expert.py` - **NEEDS CHECK**
- `journal_expert.py` - Working ✅
- `improved_birthday_expert.py` - Working ✅

### Core Services (in /home/pi/zoe/services/zoe-core/):
- `routers/chat.py` - Main chat endpoint
- `routers/reminders.py` - Reminders API endpoint (need to verify exists)
- `routers/homeassistant.py` - Smart home API
- `enhanced_mem_agent_client.py` - Client to mem-agent
- `temporal_memory_integration.py` - Temporal memory system

## Specific Debug Requests

### 1. Check Reminders API Endpoint
```bash
# Does this endpoint exist and work?
curl -s http://localhost:8000/api/reminders/ -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test reminder",
    "reminder_time": "10:00",
    "reminder_date": "2025-10-09",
    "user_id": "test",
    "reminder_type": "once",
    "category": "personal"
  }'
```

**Question:** What's the correct schema for creating a reminder? Check `/home/pi/zoe/services/zoe-core/routers/reminders.py`

### 2. Check ReminderExpert Error Details
The error log shows: `ERROR:reminder_expert:Reminder creation failed: ` (empty error message)

**Question:** Can you add exception details to see the actual error? Line 120 in `reminder_expert.py`

### 3. Fix Time Format
Current: `reminder_time = "10am"` 
Should it be: `reminder_time = "10:00"`?

**Question:** What format does the reminders API expect?

### 4. Check HomeAssistant API
```bash
# Does this work?
curl -s http://localhost:8000/api/homeassistant/control \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"action": "turn_on", "entity": "light.living_room"}'
```

**Question:** What's the correct endpoint and payload format for HomeAssistant control?

### 5. AI Model Safety Settings
The LLM is hallucinating inappropriate safety concerns. Where can I adjust this?

**Possible locations:**
- System prompt in `routers/chat.py` line 408
- Model configuration in `model_config.py`
- Ollama model parameters

## How to Test

```bash
# Quick test of specific failure
cd /home/pi/zoe
python3 -c "
import requests
resp = requests.post('http://localhost:8000/api/chat', 
                     json={'message': 'Remind me tomorrow at 10am to go shopping', 'user_id': 'test'})
print('Actions:', resp.json().get('actions_executed'))
print('Response:', resp.json().get('response')[:200])
"

# Run all 43 tests with detailed output
python3 tests/e2e/run_all_tests_detailed.py

# View detailed report
cat tests/e2e/detailed_test_report.json | python3 -m json.tool
```

## Goal
**100% pass rate (43/43 tests)** with all responses relevant to queries.

## What I Need
1. How to fix ReminderExpert API calls (will fix 6 tests immediately)
2. How to disable/adjust AI safety filters (will fix 2 tests)
3. How to verify/fix HomeAssistant and Planning endpoints (will fix 3 tests)

Please help me identify the root cause and provide fixes for these 11 failures!

