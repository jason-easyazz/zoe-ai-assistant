# Prompt for AI Assistants: Help Fix Memory System to 100% Pass Rate

## Context

I'm working on an AI assistant system called "Zoe" that needs to achieve 100% pass rate on comprehensive testing. The system is 95% complete but has critical memory retrieval issues preventing it from reaching the goal.

## Current Status

- **Pass Rate:** 32.3% (21/65 queries passed)
- **Memory Retention:** 33.3% (should be 90%+)
- **Target:** 100% pass rate

## Architecture Overview

The system has:
1. **MCP Server** (`zoe-mcp-server`) - Handles tool execution (store_self_fact, get_self_info)
2. **Chat Router** (`zoe-core/routers/chat.py`) - Main conversation handler
3. **Memory Search** (`search_memories()`) - Searches people.facts JSON field
4. **Auto-Injection** (`_auto_inject_tool_call()`) - Pattern matching for tool calls
5. **Prompt Templates** (`prompt_templates.py`) - Builds system prompts with context

## The Problem: Memory Storage Works, Retrieval Fails

### What's Working ✅

1. **Storage:** When user says "My name is Alice", the system:
   - Detects pattern via auto-injection
   - Generates `[TOOL_CALL:store_self_fact:{"fact_key":"name","fact_value":"Alice"}]`
   - Executes tool call via MCP server
   - Stores fact in `people` table, `facts` JSON field: `{"name": "Alice"}`
   - ✅ **Verified:** Facts ARE being stored in database

2. **Search:** When user asks "What is my name?", the system:
   - Calls `search_memories(query="What is my name?", user_id)`
   - Searches `people.facts` JSON field
   - Finds facts: `{"name": "Alice"}`
   - Adds to `semantic_results`: `[{"type": "self_fact", "fact_key": "name", "fact_value": "Alice", ...}]`
   - ✅ **Verified:** Logs show "✅ Found 1 self facts" and "✅ Light RAG: 5 semantic results"

3. **Prompt Building:** The system:
   - Includes semantic_results in prompt via `build_enhanced_prompt()`
   - Formats as: `**Name**: Alice`
   - ✅ **Verified:** Prompt includes the fact

### What's NOT Working ❌

**The LLM response ignores the retrieved facts!**

When user asks "What is my name?", Zoe responds:
- ❌ "I don't have any information about your name"
- ❌ "Would you like to tell me what it is?"
- ❌ **Instead of:** "Your name is Alice" (which is in the prompt!)

**The fact is in the prompt, but the LLM doesn't use it.**

## Code Flow Analysis

### Storage Flow (WORKING)
```
User: "My name is Alice"
  ↓
chat.py: _auto_inject_tool_call() detects pattern
  ↓
Generates: [TOOL_CALL:store_self_fact:{"fact_key":"name","fact_value":"Alice"}]
  ↓
parse_and_execute_tool_calls() executes
  ↓
execute_mcp_tool() calls MCP server
  ↓
MCP server: _store_self_fact() stores in people.facts JSON
  ✅ SUCCESS: Fact stored
```

### Retrieval Flow (BROKEN)
```
User: "What is my name?"
  ↓
chat.py: get_user_context() called
  ↓
search_memories() called with query="What is my name?"
  ↓
Searches people.facts JSON, finds {"name": "Alice"}
  ↓
Adds to semantic_results: [{"type": "self_fact", "fact_key": "name", "fact_value": "Alice"}]
  ↓
build_enhanced_prompt() includes in prompt:
  "⚠️ USER'S PERSONAL INFORMATION - USE THIS TO ANSWER QUESTIONS ⚠️
   **Name**: Alice
   **IMPORTANT**: If the user asks about any of the above, use the exact information provided."
  ↓
LLM receives prompt with fact
  ↓
LLM generates: "I don't have any information about your name"
  ❌ FAILURE: LLM ignores the fact that's clearly in the prompt!
```

## Specific Issues

### Issue 1: LLM Not Using Retrieved Facts
**Symptom:** Facts are in prompt but LLM says "I don't know"
**Evidence:**
- Logs show: `✅ Found 1 self facts`
- Logs show: `✅ Light RAG: 5 semantic results`
- Prompt inspection shows fact is included
- But response says "I don't have information"

**Possible Causes:**
1. Prompt format not clear enough?
2. LLM model (qwen2.5:7b, llama3.2:3b) not following instructions?
3. Temperature too high (0.7)?
4. System prompt too long, fact gets lost?
5. Model routing issue (wrong model for memory queries)?

### Issue 2: Auto-Injection for Retrieval Not Working
**Symptom:** "What is my name?" should trigger `get_self_info` tool call
**Current:** Pattern exists in `_auto_inject_tool_call()` but not being executed
**Code:**
```python
self_info_patterns = [
    (r'what\s+is\s+my\s+([a-z_\s]+?)\?', None),
    (r'what\'s\s+my\s+([a-z_\s]+?)\?', None),
]
# Generates: [TOOL_CALL:get_self_info:{"fact_key":"name"}]
```
**But:** Tool call is generated but result is ignored (same as storage issue)

### Issue 3: Speed Issues
**Symptom:** Some queries take >2s (target: <2s for voice)
- Greetings: 2.16s (target: <1s)
- Complex: 5.54s (target: <3s)

**Not critical for 100% pass rate, but should be optimized**

## Files Involved

1. **`services/zoe-core/routers/chat.py`** (Lines 392-438, 525-557, 1256-1280)
   - Memory search logic
   - Auto-injection
   - Context assembly

2. **`services/zoe-core/prompt_templates.py`** (Lines 520-535)
   - How semantic_results are formatted in prompt

3. **`services/zoe-mcp-server/main.py`** (Lines 2429-2530)
   - `_store_self_fact()` - Storage (working)
   - `_get_self_info()` - Retrieval (returns fact but LLM ignores it)

4. **`services/zoe-core/route_llm.py`**
   - Model routing (zoe-memory, zoe-chat, zoe-action)

## Test Results

From comprehensive test (65 queries):
- **Memory Personal:** 3/6 passed (50%) - Storage works, retrieval fails
- **Memory Preferences:** 2/6 passed (33%) - Same issue
- **Memory Projects:** 1/5 passed (20%) - Same issue
- **Multi-turn Memory:** 1/7 passed (14%) - Context lost

**Pattern:** Storage ✅, Retrieval ❌

## Questions for AI Assistants

1. **Why is the LLM ignoring facts that are clearly in the prompt?**
   - Is the prompt format wrong?
   - Is the model not capable?
   - Is there a conflict with other instructions?

2. **Should we use tool calls for retrieval instead of prompt injection?**
   - Current: Put facts in prompt, hope LLM uses them
   - Alternative: Call `get_self_info` tool, use result directly

3. **Is the model routing correct?**
   - Memory queries use "zoe-memory" model (qwen2.5:7b)
   - Should we use a different model?
   - Should we use different temperature?

4. **Is the prompt too long/complex?**
   - Base prompt is ~2000 lines
   - Facts added at the end
   - Does the model lose focus?

5. **Should we bypass LLM for simple fact retrieval?**
   - If query matches "What is my X?" pattern
   - Directly call `get_self_info` tool
   - Return result without LLM

## What We've Tried

1. ✅ Improved memory search matching (keyword extraction)
2. ✅ Made semantic_results more prominent in prompt (⚠️ warnings, bold formatting)
3. ✅ Added explicit instructions ("USE THIS", "Do NOT say I don't know")
4. ✅ Fixed MCP server bugs (UserContext)
5. ✅ Verified facts are stored correctly
6. ✅ Verified facts are found in search
7. ✅ Verified facts are in prompt

**None of these fixed the core issue: LLM still ignores facts.**

## Request for Help

Please analyze:
1. Why the LLM ignores facts in the prompt
2. Best approach to fix (prompt engineering, tool calls, model change, etc.)
3. Specific code changes needed
4. Alternative architectures to consider

**Goal:** Get memory retrieval working so "What is my name?" → "Your name is Alice" (when Alice was previously stored)

**Current:** "What is my name?" → "I don't have information" (even though Alice is in the prompt)

---

## Technical Details

### Environment
- Python 3.11
- FastAPI backend
- SQLite database (people table with facts JSON field)
- llama.cpp server (zoe-llamacpp) with multiple models
- MCP (Model Context Protocol) for tools

### Models Used
- `qwen2.5:7b` - For memory/action queries
- `llama3.2:3b` - For fast conversation
- Temperature: 0.7 (dynamic based on intent)

### Database Schema
```sql
CREATE TABLE people (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    name TEXT,
    facts TEXT,  -- JSON: {"name": "Alice", "favorite_color": "blue"}
    is_self INTEGER DEFAULT 0
);
```

### Key Code Snippets

**Memory Search (working):**
```python
# In search_memories()
cursor.execute("SELECT facts FROM people WHERE user_id = ? AND is_self = 1", (user_id,))
facts = json.loads(result[0])  # {"name": "Alice"}
# Adds to semantic_results ✅
```

**Prompt Building (working):**
```python
# In build_enhanced_prompt()
if memories.get("semantic_results"):
    context_section += "⚠️ USER'S PERSONAL INFORMATION ⚠️\n"
    for result in memories["semantic_results"]:
        if result.get('type') == 'self_fact':
            context_section += f"**{fact_key}**: {fact_value}\n"
    # Fact is in prompt ✅
```

**LLM Response (broken):**
```python
# LLM receives prompt with "**Name**: Alice"
# But generates: "I don't have information about your name"
# ❌ Why?
```

---

**Please help diagnose why the LLM ignores facts that are clearly provided in the prompt, and suggest concrete fixes to reach 100% pass rate.**




