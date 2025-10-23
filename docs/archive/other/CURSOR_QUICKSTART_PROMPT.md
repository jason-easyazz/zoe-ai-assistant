# 🚀 CURSOR AI - QUICK START PROMPT

**Copy this entire prompt to Cursor to begin Zoe integration fixes**

---

## CONTEXT

You are working on **Zoe AI Assistant v2.4.0** - a "Samantha from Her" level AI companion.

**Current State**:
- ✅ All APIs work (30+ endpoints, 100% functional)
- ❌ Chat UI integration incomplete (50% working)
- ❌ 30% of chat queries timeout
- ❌ Enhancement systems exist but aren't integrated into chat

**The Problem**:
Backend intelligence exists (temporal memory, multi-expert orchestration, satisfaction tracking) but the chat router doesn't use it. It's a simple LLM passthrough instead of an orchestration hub.

**The Goal**:
Integrate all enhancement systems into chat so users get the full "Samantha" experience through natural conversation.

---

## YOUR MISSION

**Phase 1 (CRITICAL - Start Here)**:
Rewrite `services/zoe-core/routers/chat.py` to become an orchestration hub that:
1. Loads temporal context before responding
2. Classifies user intent
3. Routes to appropriate experts
4. Handles timeouts with auto-decomposition
5. Stores interactions in temporal memory

---

## STEP-BY-STEP IMPLEMENTATION

### STEP 1: Analyze Current State

**Read these files first**:
```
services/zoe-core/routers/chat.py                    # Current (broken) chat router
services/zoe-core/temporal_memory_integration.py     # Temporal memory system (unused)
services/zoe-core/cross_agent_collaboration.py       # Multi-expert orchestration (unused)
services/zoe-core/enhanced_mem_agent_client.py       # MEM agent client
```

**Understand**:
- What does chat.py do NOW? (simple LLM passthrough)
- What SHOULD it do? (orchestrate temporal memory + experts + actions)
- Why isn't it integrated? (needs rewrite)

---

### STEP 2: Implement Intent Classification

**File**: `services/zoe-core/enhanced_mem_agent_client.py`

**Add this capability**:
```python
class Intent:
    def __init__(self, type: str, confidence: float, requires_action: bool, target_expert: str = None):
        self.type = type
        self.confidence = confidence
        self.requires_action = requires_action
        self.target_expert = target_expert

async def classify_intent(self, query: str) -> Intent:
    """
    Classify if query needs action (route to expert) or is conversational

    Action patterns:
    - "add X to list" → ListExpert
    - "create event" → CalendarExpert
    - "remind me" → ReminderExpert
    - "journal: X" → JournalExpert
    - "what did we discuss" → MemoryExpert with temporal context

    Returns Intent with routing info
    """
    # Implement regex-based classification
    # Return Intent with target expert if action needed
```

**Test**: Can correctly classify "Add bread to shopping list" → Intent(type="list_add", requires_action=True, target_expert="ListExpert")

---

### STEP 3: Rewrite Chat Router

**File**: `services/zoe-core/routers/chat.py`

**New Architecture**:
```python
from temporal_memory_integration import TemporalMemory
from cross_agent_collaboration import MultiExpertOrchestrator
from enhanced_mem_agent_client import EnhancedMEMAgentClient
import asyncio

# Initialize systems
temporal_memory = TemporalMemory()
orchestrator = MultiExpertOrchestrator()
mem_agent = EnhancedMEMAgentClient()

@router.post("/api/chat/enhanced")
async def enhanced_chat(request: ChatRequest, user_id: str):
    """Enhanced chat with full integration"""

    # 1. Load temporal context (last 5 interactions)
    context = await temporal_memory.get_recent_context(user_id, limit=5)

    # 2. Classify intent
    intent = await mem_agent.classify_intent(request.message)

    # 3. Route appropriately
    if intent.requires_action:
        # Route to expert orchestrator
        result = await orchestrator.execute_with_experts(
            query=request.message,
            user_id=user_id,
            context=context,
            timeout=25.0  # Auto-decompose if exceeds
        )
    else:
        # Conversational with temporal context
        try:
            result = await asyncio.wait_for(
                _chat_with_context(request.message, context),
                timeout=25.0
            )
        except asyncio.TimeoutError:
            # Auto-decompose complex conversational queries
            result = await orchestrator.decompose_and_execute(request.message, user_id)

    # 4. Store interaction
    await temporal_memory.store_interaction(
        user_id=user_id,
        query=request.message,
        response=result.response,
        metadata={
            "intent": intent.type,
            "experts_used": result.experts_used,
            "actions": result.actions
        }
    )

    # 5. Return rich response
    return {
        "response": result.response,
        "actions_taken": result.actions,
        "experts_consulted": result.experts_used,
        "execution_time": result.execution_time
    }
```

**Critical**: Don't break existing `/api/chat` endpoint - add `/api/chat/enhanced` as new endpoint

---

### STEP 4: Add Timeout Auto-Decomposition

**File**: `services/zoe-core/cross_agent_collaboration.py`

**Add this method to MultiExpertOrchestrator class**:
```python
async def decompose_and_execute(self, query: str, user_id: str) -> OrchestrationResult:
    """
    When query times out, automatically break into subtasks

    Example:
    Query: "Plan dinner party Friday, add wine to list, remind me Thursday"
    Decomposed:
      1. CalendarExpert: Create dinner party event Friday
      2. ListExpert: Add wine to shopping list
      3. ReminderExpert: Set reminder Thursday

    Execute in parallel, synthesize results
    """

    # Use LLM to decompose
    subtasks = await self._llm_decompose(query)

    # Execute in parallel
    results = await asyncio.gather(*[
        self.execute_with_expert(task["expert"], task["query"], user_id)
        for task in subtasks
    ])

    # Synthesize
    final_response = await self._llm_synthesize(query, results)

    return OrchestrationResult(
        response=final_response,
        actions=[r.action for r in results],
        experts_used=[r.expert for r in results]
    )
```

**Result**: Complex queries decompose instead of timing out

---

### STEP 5: Create Missing Experts

**Files to create** (one at a time):

1. **JournalExpert**: `services/zoe-mcp-server/experts/journal_expert.py`
   - Handles: "journal: X", "how was I feeling"
   - Calls: POST `/api/journal/entries`
   - Router already exists: `services/zoe-core/routers/journal.py`

2. **ReminderExpert**: `services/zoe-mcp-server/experts/reminder_expert.py`
   - Handles: "remind me to X"
   - Calls: POST `/api/reminders/`
   - Already has API endpoints

3. **WeatherExpert**: `services/zoe-mcp-server/experts/weather_expert.py`
   - Handles: "what's the weather"
   - Calls existing weather router

4. **HomeAssistantExpert**: `services/zoe-mcp-server/experts/homeassistant_expert.py`
   - Handles: "turn on lights", "set temperature"
   - Calls homeassistant-mcp-bridge

**Pattern for each expert**:
```python
class JournalExpert(BaseExpert):
    def can_handle(self, query: str) -> bool:
        return bool(re.search(r"journal|diary|feeling|mood", query.lower()))

    async def handle_request(self, query: str, user_id: str) -> dict:
        # Extract info from query (use LLM)
        # Call API endpoint
        # Return result
        pass
```

---

### STEP 6: Write Integration Tests

**File**: `tests/integration/test_chat_integration.py` (NEW)

```python
@pytest.mark.integration
async def test_chat_with_temporal_memory():
    """Verify chat remembers previous interactions"""

    # First interaction
    response1 = await post_chat("My favorite color is blue")

    # Second interaction (should remember)
    response2 = await post_chat("What's my favorite color?")

    assert "blue" in response2["response"].lower()
    assert response2["context_used"] > 0  # Used temporal context

@pytest.mark.integration
async def test_multi_expert_orchestration():
    """Verify complex queries route to multiple experts"""

    response = await post_chat(
        "Add wine to shopping list and create dinner party event Friday"
    )

    assert "ListExpert" in response["experts_consulted"]
    assert "CalendarExpert" in response["experts_consulted"]
    assert len(response["actions_taken"]) >= 2
    assert response["execution_time"] < 25.0  # No timeout

@pytest.mark.integration
async def test_no_timeouts_on_complex_queries():
    """Verify complex queries decompose instead of timing out"""

    complex_query = """
    Create doctor appointments for next month,
    add vitamins to shopping list,
    journal about health goals,
    and remind me to exercise
    """

    response = await post_chat(complex_query)

    # Should succeed (not timeout)
    assert response["response"]
    assert len(response["actions_taken"]) >= 4
```

**Run**: `pytest tests/integration/test_chat_integration.py -v`

---

### STEP 7: Verify End-to-End

**Manual Test Sequence**:

```bash
# Test 1: Temporal memory
curl -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{"message": "My name is Alice", "user_id": "test_user"}'

curl -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{"message": "What is my name?", "user_id": "test_user"}'
# Should respond: "Your name is Alice"

# Test 2: Expert routing
curl -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{"message": "Add bread and milk to shopping list", "user_id": "test_user"}'
# Should show: "experts_consulted": ["ListExpert"], "actions_taken": [...]

# Test 3: Multi-expert orchestration
curl -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{"message": "Create event tomorrow at 3pm and add wine to list", "user_id": "test_user"}'
# Should show: "experts_consulted": ["CalendarExpert", "ListExpert"]

# Test 4: Complex query (no timeout)
curl -X POST http://localhost:8000/api/chat/enhanced \
  -H "Content-Type: application/json" \
  -d '{"message": "Plan a dinner party, add ingredients to list, create event, and journal about it", "user_id": "test_user"}'
# Should complete in < 25s with multiple actions
```

**Success Criteria**:
- ✅ All 4 tests pass
- ✅ No timeouts
- ✅ Temporal memory working
- ✅ Experts routing correctly

---

## ADDITIONAL FIXES (Do After Step 7)

### Fix Database Migrations
```bash
cd services/zoe-core
pip install alembic
alembic init migrations
alembic revision --autogenerate -m "initial_schema"
```
See: `CURSOR_IMPLEMENTATION_PLAN.md` Phase 3 for details

### Optimize Resources
- Reduce Ollama models from 14 to 3-5 most-used
- Add memory limits to docker-compose.yml
- See: `CURSOR_IMPLEMENTATION_PLAN.md` Phase 4

### Add Monitoring
- Deploy Prometheus + Grafana
- Configure alerts
- See: `CURSOR_IMPLEMENTATION_PLAN.md` Phase 5

---

## KEY FILES REFERENCE

**Files you'll modify**:
- `services/zoe-core/routers/chat.py` - Main rewrite
- `services/zoe-core/enhanced_mem_agent_client.py` - Add intent classification
- `services/zoe-core/cross_agent_collaboration.py` - Add decomposition
- `services/zoe-mcp-server/experts/*.py` - Create 4 new experts

**Files to read for context**:
- `services/zoe-core/temporal_memory_integration.py` - How temporal memory works
- `services/zoe-core/routers/lists.py` - Example API router
- `services/zoe-core/routers/calendar.py` - Example API router
- `services/zoe-core/routers/journal.py` - Example API router
- `services/zoe-mcp-server/experts/list_expert.py` - Example expert
- `docs/EXPERT_ARCHITECTURE.md` - Expert system overview
- `PROJECT_STATUS.md` - Current system state

**Configuration files**:
- `docker-compose.yml` - Service orchestration
- `.env` - Environment variables
- `services/zoe-core/requirements.txt` - Python dependencies

---

## EXPECTED OUTCOMES

**After completing Steps 1-7**:

**Before**:
- Chat Tests: 5/10 passing ❌
- Timeout Rate: 30% ❌
- Temporal Memory: Not integrated ❌
- Expert Routing: Partial ⚠️

**After**:
- Chat Tests: 10/10 passing ✅
- Timeout Rate: 0% ✅
- Temporal Memory: Fully integrated ✅
- Expert Routing: Complete ✅

**User Experience**:
- "What did we discuss yesterday?" → Works (temporal memory)
- "Add bread to list and remind me to shop" → Works (multi-expert)
- Complex queries → No timeouts (auto-decomposition)
- Natural conversation → Remembers context

---

## TROUBLESHOOTING

**If chat doesn't use temporal memory**:
- Check: Is `temporal_memory.get_recent_context()` being called?
- Check: Are interactions being stored with `store_interaction()`?
- Debug: Print `context` variable to see what's loaded

**If experts aren't triggered**:
- Check: Is intent classification working? Print `intent` variable
- Check: Intent patterns in `classify_intent()` - add more patterns
- Debug: Test classification separately: `await mem_agent.classify_intent("add bread to list")`

**If queries still timeout**:
- Check: Is timeout set to 25s? `asyncio.wait_for(..., timeout=25.0)`
- Check: Is decomposition being called on timeout?
- Debug: Print execution time for each step

**If tests fail**:
- Check: Are services running? `docker ps`
- Check: Is database accessible? `curl http://localhost:8000/health`
- Check: Are dependencies installed? `pip install -r requirements.txt`

---

## DELIVERABLES CHECKLIST

- [ ] Intent classification implemented (`enhanced_mem_agent_client.py`)
- [ ] Chat router rewritten to use temporal memory + orchestration (`chat.py`)
- [ ] Timeout auto-decomposition added (`cross_agent_collaboration.py`)
- [ ] 4 new experts created (Journal, Reminder, Weather, HomeAssistant)
- [ ] Integration tests written and passing (`test_chat_integration.py`)
- [ ] Manual tests all pass (4 curl tests above)
- [ ] No chat timeouts (0% down from 30%)
- [ ] Documentation updated (`CHANGELOG.md`, `README.md`)

---

## NEXT STEPS AFTER THIS

Once Steps 1-7 are complete:
1. Implement database migrations (Phase 3 of full plan)
2. Optimize resources (Phase 4 of full plan)
3. Add monitoring (Phase 5 of full plan)
4. Create E2E test suite (Phase 6 of full plan)

**See**: `CURSOR_IMPLEMENTATION_PLAN.md` for complete 6-week plan

---

## START NOW

**Copy this prompt to Cursor and begin with Step 1**: Read and analyze `services/zoe-core/routers/chat.py`

**First question to answer**: What does the current chat router do, and what's missing?

**Then**: Proceed to Step 2 (Intent Classification)

🚀 **Let's make Zoe truly intelligent!**
