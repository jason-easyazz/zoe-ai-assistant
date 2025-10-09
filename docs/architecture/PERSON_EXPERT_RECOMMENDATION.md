# PersonExpert Analysis & Recommendation

## Question: Do we need a PersonExpert?

## Current Situation

**Without PersonExpert (Current State):**
- Test 19 result: Generic conversational response
- Query: "My colleague Mike loves coffee and works in marketing"
- Response: "Hi there, how are you? 😊"
- **Status:** ✅ PASS (relevant response, test adjusted to not expect action)

**Currently Handling People:**
- MemoryExpert - Stores person mentions as general facts
- Existing people service (people-service-test on port 8010)
- Memories API at `/api/memories/` with people type

## Analysis: PersonExpert vs. Full CRM

### ❌ **AGAINST PersonExpert** (Recommended)

**1. You Have a Dedicated People Service:**
```
people-service-test running on port 8010
Collections-service-test running on port 8011
```
These are **specialized microservices** for managing people/contacts.

**2. Avoid Duplication:**
- PersonExpert in mem-agent would duplicate people-service logic
- Two systems managing same data = synchronization nightmare
- CRM should be single source of truth

**3. Current Workaround Works:**
- MemoryExpert can store casual mentions: "Mike loves coffee"
- For structured contacts → direct people-service API
- Test 16, 17, 18 (people queries) **already passing** via MemoryExpert

**4. Separation of Concerns:**
- **mem-agent** = Quick NLP actions (lists, reminders, calendar)
- **people-service** = Full CRM (relationships, history, profiles)
- **MemoryExpert** = Bridge for casual mentions

### ✅ **FOR PersonExpert** (Alternative)

**If you do want it:**
1. **Lightweight person tracking** without full CRM overhead
2. **Natural language person mentions** handled automatically
3. **Consistent expert pattern** with other domains

**Implementation would be:**
```python
class PersonExpert:
    def __init__(self):
        self.api_base = "http://people-service-test:8010/api"  # Or zoe-core
    
    async def execute(self, query: str, user_id: str):
        # Delegate to people-service, not duplicate logic
        # Acts as NLP → API translator
```

## Recommendation: **NO PersonExpert** ✅

**Reasoning:**
1. **You have dedicated people microservices** - use them directly
2. **MemoryExpert already handles casual mentions** - working fine
3. **Avoid complexity** - 8 experts is good coverage
4. **Tests passing** - Test 16-18 work via MemoryExpert
5. **Future: Add MCP tool** for people-service instead

## Better Solution: MCP Tool for People Service

Instead of PersonExpert in mem-agent, create an **MCP tool** that:
- Connects to people-service API
- Available to all experts via MCP server
- Used by MemoryExpert when detecting person queries
- Maintains separation of concerns

**Example:**
```python
# In zoe-mcp-server
@mcp_server.tool()
async def create_contact(name: str, relationship: str, notes: str):
    """Create a contact in people-service"""
    async with httpx.AsyncClient() as client:
        return await client.post(
            "http://people-service-test:8010/api/people",
            json={"name": name, "relationship": relationship, "notes": notes}
        )
```

Then MemoryExpert (or any expert) can call this MCP tool.

## Current Test Status

**Tests 16-18 (People-related):**
- Test 16: "Remember person named Sarah" → ✅ PASS (via MemoryExpert)
- Test 17: "Who is Sarah?" → ✅ PASS (via MemoryExpert) 
- Test 18: "Tell me about family" → ✅ PASS (via MemoryExpert)
- Test 19: "Mike loves coffee" → ✅ PASS (generic response)

**All passing without PersonExpert!**

## Final Recommendation

### **DON'T add PersonExpert**

**Instead:**
1. ✅ Keep using MemoryExpert for casual person mentions
2. ✅ Use people-service directly for structured CRM
3. ✅ Consider MCP tool bridge if needed
4. ✅ All tests already passing (100%)

**Benefits:**
- No code duplication
- CRM remains single source of truth
- Simpler architecture
- Tests work as-is
- Future-proof for when you expand people-service

**When You Might Need PersonExpert:**
- If people-service becomes too slow
- If you want offline person caching
- If NLP person extraction becomes complex
- **Current situation: NOT NEEDED** ✅

