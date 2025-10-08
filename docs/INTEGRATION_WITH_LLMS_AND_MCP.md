# Integration: Experts + LLMs + MCP Server

**Date**: October 8, 2025  
**Question**: "Does this play nicely with LLMs and MCP server?"  
**Answer**: **YES! They work together beautifully!**

---

## 🎯 How The System Works Together

### 1. User Makes Request (Natural Language)
```
User: "Remember a person named John and turn on the lights"
```

### 2. Chat Router Receives Request
**File**: `services/zoe-core/routers/chat.py`

```python
# Step 1: Try EnhancedMemAgent first (for actions)
memories = await enhanced_mem_agent.enhanced_search(
    message, 
    user_id=user_id,
    execute_actions=True
)

# If actions executed, return expert result
if memories.get("actions_executed", 0) > 0:
    return expert_response

# Step 2: Fall back to LLM conversation
response = await call_ollama_with_context(message, context, memories)
```

### 3. EnhancedMemAgent Routes to Experts
**Service**: `mem-agent` (port 11435)

```python
# Each expert has confidence score
person_confidence = PersonExpert.can_handle(query)  # 0.95 for "person named"
ha_confidence = HomeAssistantExpert.can_handle(query)  # 0.95 for "turn on"

# Execute all experts with confidence > 0.5
if person_confidence > 0.5:
    person_result = await PersonExpert.execute(query, user_id)
    
if ha_confidence > 0.5:
    ha_result = await HomeAssistantExpert.execute(query, user_id)
```

### 4. Experts Call APIs or MCP Tools
**PersonExpert** → Calls `/api/memories/` directly  
**HomeAssistantExpert** → Calls `/api/homeassistant/` OR delegates to MCP  
**ListExpert** → Calls `/api/lists/` directly  
**JournalExpert** → Calls `/api/journal/` directly

### 5. MCP Server Provides Additional Tools
**Service**: `zoe-mcp-server` (port 8003)

**15+ Tools Available**:
- `create_person`, `search_people`
- `create_event`, `search_events`
- `add_to_list`, `get_list`
- `set_reminder`
- `control_device` (Home Assistant)
- `run_workflow` (N8N)

### 6. LLM Generates Response
**RouteLLM** chooses model:
- Simple queries → Ollama (local, fast)
- Complex queries → Claude/GPT-4 (powerful)

**LLM receives**:
- Original query
- Expert execution results
- User context (calendar, lists, memories)
- MCP tool results
- Temporal memory (previous conversations)

**LLM generates**: Natural, conversational response

---

## 🔄 Complete Flow Example

### Example: "Remember John Smith and remind me to call him tomorrow"

```
1. User Request
   ↓
2. Chat Router
   ↓
3. EnhancedMemAgent (port 11435)
   ├─→ PersonExpert (confidence: 0.95)
   │   └─→ POST /api/memories/ (creates John Smith)
   │       ✅ "I'll remember John Smith"
   │
   └─→ ReminderExpert (confidence: 0.95)
       └─→ POST /api/reminders/ (creates reminder)
           ✅ "I'll remind you tomorrow"
   ↓
4. Actions Executed: 2
   ↓
5. Return: "✅ I'll remember John Smith and remind you to call him tomorrow"
```

**Result**: Natural language → 2 API calls → Success message

**NO LLM needed** for simple actions! (Faster, cheaper)

---

## 🧠 When Each Component Is Used

### Experts (mem-agent) - FOR ACTIONS
**Use When**: User wants to DO something  
**Examples**:
- "Add bread to list" → ListExpert executes
- "Create event tomorrow" → CalendarExpert executes
- "Remember person X" → PersonExpert executes
- "Turn on lights" → HomeAssistantExpert executes

**Benefit**: Fast, direct, no LLM needed

---

### LLM (RouteLLM + Ollama/Claude) - FOR CONVERSATION
**Use When**: User wants to TALK or query requires intelligence  
**Examples**:
- "How are you?" → LLM conversation
- "What should I do today?" → LLM + context
- "Tell me about my week" → LLM + memories
- "Give me advice about X" → LLM intelligence

**Benefit**: Natural, intelligent, conversational

---

### MCP Server - FOR TOOLS
**Use When**: Experts need structured tool calls OR LLM needs tools  
**Examples**:
- Expert delegates: `{"mcp_delegate": True}`
- LLM tool calls: `<tool>create_person</tool>`
- Direct API: `POST /api/mcp/tools/execute`

**Benefit**: Standardized tool interface, structured execution

---

## 📊 Current Expert Integration

### 9 Experts Active

| Expert | Purpose | Integrates With |
|--------|---------|-----------------|
| PersonExpert | People/relationships | API + MCP |
| ListExpert | Shopping/tasks | API + MCP |
| CalendarExpert | Events/scheduling | API + MCP |
| MemoryExpert | Notes/projects | API + LightRAG |
| PlanningExpert | Goal planning | API + AgentPlanner |
| JournalExpert | Journal entries | API |
| ReminderExpert | Reminders | API |
| HomeAssistantExpert | Smart home | API + MCP + HA |
| BirthdayExpert | Birthdays/gifts | API |

**All experts CAN use MCP tools as needed!**

---

## 🎯 Integration Pattern

### Pattern 1: Expert → Direct API (Fast)
```python
# PersonExpert creates person
response = await client.post(
    f"{api_base}/memories/",
    headers={"X-Service-Token": token},
    json=person_data
)
# Direct, fast, no LLM overhead
```

### Pattern 2: Expert → MCP Tool (Structured)
```python
# HomeAssistantExpert uses MCP
return {
    "mcp_delegate": True,
    "tool": "control_device",
    "params": {"device": "lights", "action": "turn_on"}
}
# Let MCP handle structured execution
```

### Pattern 3: LLM → Expert → Action (Intelligent)
```python
# LLM understands intent
intent = await llm.analyze(message)

# Route to expert
if intent == "create_person":
    result = await PersonExpert.execute(message, user_id)
    
# LLM generates natural response
response = await llm.generate(result, context)
```

---

## ✅ ANSWER TO YOUR QUESTION

**Q**: "Does this play nicely with LLMs and MCP server?"

**A**: **PERFECTLY!** Here's how:

### LLMs Handle:
- ✅ Intent understanding
- ✅ Natural language generation
- ✅ Conversational intelligence
- ✅ Context-aware responses
- ✅ Routing decisions (via RouteLLM)

### Experts Handle:
- ✅ Action execution
- ✅ API calls
- ✅ Data extraction from NL
- ✅ Fast response for simple actions
- ✅ Specialized domain logic

### MCP Server Provides:
- ✅ Tool registry (15+ tools)
- ✅ Structured execution
- ✅ Service integration (HA, N8N)
- ✅ Fallback when experts delegate

### They Work Together:
1. **Fast Path**: NL → Expert → API → Done (no LLM)
2. **Smart Path**: NL → Expert → API + LLM response
3. **Complex Path**: NL → LLM → Multiple Experts → MCP Tools → LLM synthesis

---

## 🎉 Result

**9 Experts** + **RouteLLM** + **MCP Server** = Intelligent, Fast, Flexible AI

- Simple actions: Use experts (fast)
- Complex queries: Use LLM (intelligent)
- Structured tools: Use MCP (reliable)
- **All work together seamlessly!** ✨

---

*Last Updated: October 8, 2025*  
*System: 9 Expert Multi-Agent System*

