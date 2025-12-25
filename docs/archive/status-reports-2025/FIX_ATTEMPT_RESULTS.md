# Fix Attempt Results - December 8, 2025

## 🎯 Attempted Fixes

### Fix #1: Calendar Routing
**Changes Made:**
1. Updated prompt_templates.py with explicit calendar examples
2. Added priority calendar keyword detection in routing fallback
3. Added tool_hint for calendar events

**Result:** ❌ **FAILED** - Still going to shopping list

**Why It Failed:**
The LLM is generating tool calls BEFORE seeing the prompt instructions. The routing happens, but the actual tool selection is done by the LLM which isn't following the semantic boundaries.

### Fix #2: Self-Facts Recall
**Changes Made:**
1. Added `self_facts` to all empty user_context dictionaries
2. Added debug logging to track self_facts

**Result:** ❌ **FAILED** - Still not recalling facts

**Why It Failed:**
Debug logs never appear, which means `build_system_prompt()` isn't being called for these queries. They're being routed to "action" mode which uses a different prompt building path.

---

## 🔍 Root Cause Analysis

### Calendar Issue - Deeper Problem
The problem isn't routing - it's that the LLM is choosing the wrong tool. Even when we route to "action" and provide tool descriptions, the LLM sees both `add_to_list` and `create_calendar_event` and chooses wrong.

**Real Solution Needed:**
1. Pre-parse the message for calendar keywords BEFORE LLM
2. If calendar keywords detected, FORCE `create_calendar_event` tool
3. Don't let LLM choose - we choose for it

### Self-Facts Issue - Deeper Problem
Facts are stored but queries like "What's my favorite food?" are being routed to MCP tool `get_self_info` instead of conversation mode. The tool returns "I'm not sure" because it's a separate system.

**Real Solution Needed:**
1. Either: Make `get_self_info` tool actually query the self_facts table
2. Or: Improve routing to send recall questions to conversation mode
3. Or: Include self_facts in action prompts too

---

## 📊 Current State

| Issue | Attempted Fix | Result | Real Fix Needed |
|-------|---------------|--------|-----------------|
| Calendar → Shopping List | Prompt updates + routing | ❌ Failed | Force tool selection |
| Self-Facts Not Recalled | Add to user_context | ❌ Failed | Fix MCP tool or routing |
| Self-Facts Extraction | Already working | ✅ Works | None |
| Shopping Lists | Already working | ✅ Works | None |

---

## 🚀 Next Steps (What Actually Needs to Happen)

### For Calendar (High Priority - Andrew's Issue)
**Option A: Force Tool Selection (Recommended)**
```python
# In chat.py, before LLM call:
if any(word in message.lower() for word in ['appointment', 'meeting', 'dentist', 'doctor']):
    # Don't ask LLM - force calendar tool
    return execute_calendar_tool(message, user_id)
```

**Option B: Post-Process LLM Output**
```python
# After LLM generates response:
if 'add_to_list' in response and any(word in original_message for word in calendar_keywords):
    # LLM chose wrong tool - override it
    response = response.replace('add_to_list', 'create_calendar_event')
```

### For Self-Facts (High Priority - Memory System)
**Option A: Fix get_self_info MCP Tool**
Make the MCP tool actually query self_facts table instead of returning "I'm not sure"

**Option B: Route Recall to Conversation**
```python
# In routing logic:
recall_patterns = ["what's my", "what is my", "do i", "where do i"]
if any(pattern in message.lower() for pattern in recall_patterns):
    routing["type"] = "conversation"  # Force conversation mode
```

**Option C: Include Self-Facts in Action Prompts**
Add self_facts section to action prompt building, not just conversation prompts

---

## ⏰ Time Estimate

- **Calendar Force-Fix:** 30 minutes
- **Self-Facts MCP Tool Fix:** 45 minutes  
- **Self-Facts Routing Fix:** 30 minutes
- **Testing:** 30 minutes

**Total:** 2-2.5 hours for complete fix

---

## 💡 Recommendation

The current approach of "improving prompts" isn't working because:
1. LLMs don't always follow instructions perfectly
2. The routing is complex with multiple paths
3. Tool selection happens in ways we don't fully control

**Better Approach:**
- Use deterministic logic for critical features (calendar, memory)
- Only use LLM for ambiguous cases
- Don't trust LLM to choose between similar tools

**This is a fundamental architecture issue, not just a prompt engineering problem.**

