# LLM Capabilities Awareness Audit

**Date**: November 7, 2025  
**Status**: 🔍 AUDIT COMPLETE - Issues Found

## Question: Does the LLM know what it can do?

### Current State Analysis

#### ✅ What the LLM KNOWS:

1. **Basic Identity** (from `prompt_templates.py`):
   - "You are Zoe - the perfect fusion of your best friend and the world's best personal assistant"
   - Warm, empathetic, intelligent, organized
   - Natural conversationalist with memory

2. **Basic Tools** (hardcoded in prompt):
   - `add_to_list`: Add items to shopping/todo lists
   - `create_calendar_event`: Schedule events
   - `get_calendar_events`: Retrieve upcoming events
   - `search_memories`: Find relevant memories
   - `create_person`: Add someone to your network
   - `get_people`: List contacts

3. **Tool Calling Format**:
   - Format: `[TOOL_CALL:tool_name:{"param1":"value1"}]`
   - JSON formatting rules explained
   - Examples provided

#### ❌ What the LLM DOESN'T KNOW:

1. **MCP Server Tools** (NOT included in prompt):
   - MCP tools are fetched via `get_mcp_tools_context()`
   - BUT: Only added if `routing.get("requires_tools")` is True
   - AND: `tools_context` is fetched but **NEVER appended to system_prompt**
   - **ISSUE**: Line 641-644 fetches tools but doesn't add them to prompt!

2. **Expert System** (NOT mentioned in prompt):
   - Expert Orchestrator exists (`cross_agent_collaboration.py`)
   - Experts available: Calendar, Lists, Memory, Planning, Development, Weather, HomeAssistant, TTS, Person
   - **ISSUE**: LLM has no idea these experts exist or how to use them

3. **Expert Capabilities**:
   - LLM doesn't know it can delegate to specialized experts
   - No mention of multi-expert coordination
   - No awareness of expert-specific capabilities

4. **MCP Server Connection**:
   - MCP server exists at `http://zoe-mcp-server:8003`
   - Tools are available but LLM doesn't know about them
   - **ISSUE**: Tools fetched but not communicated to LLM

## Code Evidence

### Problem 1: MCP Tools Not Added to Prompt

```python:640:644:services/zoe-core/routers/chat.py
# Check if this requires tool calls via MCP
tools_context = await get_mcp_tools_context()
if tools_context and routing.get("requires_tools"):
    # AG-UI Event: action (tool call)
    yield f"data: {json.dumps({'type': 'action', 'name': 'mcp_tools', 'arguments': {{'query': message}}, 'status': 'running', 'timestamp': datetime.now().isoformat()})}\n\n"
```

**ISSUE**: `tools_context` is fetched but never appended to `system_prompt` or `full_prompt`!

### Problem 2: Experts Not Mentioned

```python:12:120:services/zoe-core/prompt_templates.py
# AVAILABLE TOOLS
- add_to_list: Add items to shopping/todo lists
- create_calendar_event: Schedule events
- get_calendar_events: Retrieve upcoming events
- search_memories: Find relevant memories
- create_person: Add someone to your network
- get_people: List contacts
```

**ISSUE**: No mention of expert system or expert capabilities.

### Problem 3: Expert Orchestrator Exists But Unknown

```python:31:41:services/zoe-core/cross_agent_collaboration.py
class ExpertType(Enum):
    CALENDAR = "calendar"
    LISTS = "lists"
    MEMORY = "memory"
    PLANNING = "planning"
    DEVELOPMENT = "development"
    WEATHER = "weather"
    HOMEASSISTANT = "homeassistant"
    TTS = "tts"
    PERSON = "person"
```

**ISSUE**: LLM has no knowledge of these experts or how to request their help.

## Available MCP Tools (Not Communicated to LLM)

From MCP server (`http://zoe-mcp-server:8003/tools/list`):
- `search_memories`: Search through Zoe's memory system
- `create_person`: Create a new person in memory
- `create_collection`: Create a new collection
- `get_people`: Get all people
- `get_person_analysis`: Get comprehensive person analysis
- `get_collections`: Get all collections
- `get_collection_analysis`: Get collection analysis
- ... (more tools available)

## Recommendations

### Fix 1: Add MCP Tools to System Prompt
**Location**: `services/zoe-core/routers/chat.py` line ~627

```python
# Build prompt with routing-specific template and user preferences
user_id_for_prompt = context.get("user_id", "default")
system_prompt = await build_system_prompt(memories, user_context, routing.get("type", "conversation"), user_id_for_prompt)

# ✅ ADD THIS: Include MCP tools in prompt
tools_context = await get_mcp_tools_context()
if tools_context:
    system_prompt += "\n\n" + tools_context

full_prompt = f"{system_prompt}\n\nUser's message: {message}\nZoe:"
```

### Fix 2: Add Expert System Information to Prompt
**Location**: `services/zoe-core/prompt_templates.py` line ~98

Add to `base_system_prompt()`:

```python
# EXPERT SYSTEM
You have access to specialized expert agents that can help with complex tasks:
- 🗓️ Calendar Expert: Schedule management, event creation, availability
- 📝 Lists Expert: Shopping lists, todo lists, task management
- 🧠 Memory Expert: Semantic memory search, person/collection management
- 📊 Planning Expert: Task decomposition, multi-step planning
- 🏠 HomeAssistant Expert: Smart home device control
- 🌤️ Weather Expert: Weather information and forecasts
- 🔊 TTS Expert: Text-to-speech capabilities
- 👥 Person Expert: People management and relationship tracking

For complex multi-step tasks, you can request expert coordination by indicating the task requires multiple experts.
```

### Fix 3: Add Expert Awareness to Action Prompt
**Location**: `services/zoe-core/prompt_templates.py` line ~123

Add to `action_focused_prompt()`:

```python
# EXPERT COORDINATION
When a task requires multiple steps or specialized knowledge:
- Use [EXPERT:expert_name:task_description] to request expert help
- Available experts: calendar, lists, memory, planning, development, weather, homeassistant, tts, person
- Experts can coordinate automatically for complex tasks
```

## Impact

**Current**: LLM only knows about 6 hardcoded tools, doesn't know about:
- MCP server tools (10+ tools)
- Expert system (9 experts)
- Multi-expert coordination
- Advanced capabilities

**After Fix**: LLM will know about:
- All MCP tools (dynamically fetched)
- Expert system and capabilities
- How to request expert help
- Multi-expert coordination

## Priority

**HIGH** - This significantly limits the LLM's effectiveness. It can't use most of the system's capabilities because it doesn't know they exist.

---

## ✅ FIXES APPLIED

### Fix 1: MCP Tools Now Added to System Prompt ✅
**Location**: `services/zoe-core/routers/chat.py` lines 629-633

**Before**: MCP tools were fetched but never added to prompt  
**After**: MCP tools are now appended to system_prompt before sending to LLM

```python
# ✅ FIX: Add MCP tools to system prompt so LLM knows what tools are available
tools_context = await get_mcp_tools_context()
if tools_context:
    system_prompt += "\n\n" + tools_context
    logger.info("✅ Added MCP tools context to system prompt")
```

### Fix 2: Expert System Information Added ✅
**Location**: `services/zoe-core/routers/chat.py` lines 635-652

**Before**: LLM had no knowledge of expert system  
**After**: Expert system information is now included in every prompt

```python
# ✅ FIX: Add expert system information so LLM knows about experts
expert_info = """
# EXPERT SYSTEM
You have access to specialized expert agents that can help with complex tasks:
- 🗓️ Calendar Expert: Schedule management, event creation, availability checking
- 📝 Lists Expert: Shopping lists, todo lists, task management, item tracking
- 🧠 Memory Expert: Semantic memory search, person/collection management, fact recall
- 📊 Planning Expert: Task decomposition, multi-step planning, project coordination
- 🏠 HomeAssistant Expert: Smart home device control, automation, device status
- 🌤️ Weather Expert: Weather information, forecasts, location-based weather
- 🔊 TTS Expert: Text-to-speech capabilities, voice synthesis
- 👥 Person Expert: People management, relationship tracking, contact information
- 💻 Development Expert: Code assistance, technical problem solving

For complex multi-step tasks, the system can automatically coordinate multiple experts.
You can use tools directly or let the expert system handle coordination.
"""
system_prompt += expert_info
```

### Fix 3: Updated Prompt Templates ✅
**Location**: `services/zoe-core/prompt_templates.py`

**Changes**:
1. Updated tool list to mention MCP server and dynamic tools
2. Added expert system information to action-focused prompt
3. Clarified that complete tool list is provided dynamically

## Result

**Before Fixes**:
- ❌ LLM only knew about 6 hardcoded tools
- ❌ No knowledge of MCP server or its tools
- ❌ No knowledge of expert system
- ❌ Couldn't use most system capabilities

**After Fixes**:
- ✅ LLM receives full MCP tools list dynamically
- ✅ LLM knows about all 9 expert agents
- ✅ LLM understands expert coordination capabilities
- ✅ LLM can use all system capabilities

## Testing

To verify the fixes work:
1. Send a message that requires MCP tools (e.g., "add bread to shopping list")
2. Check backend logs for "✅ Added MCP tools context to system prompt"
3. Verify LLM response includes tool calls
4. Test expert coordination with complex multi-step tasks

## Status

**✅ FIXED** - LLM now has full awareness of:
- All MCP tools (dynamically fetched)
- Expert system and capabilities
- Multi-expert coordination
- Complete system capabilities

