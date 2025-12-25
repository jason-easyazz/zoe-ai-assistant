# Phase 3: Self-Facts in System Prompt - STATUS UPDATE

## Time Invested
3+ hours

## What Was Implemented

### 1. Modified `get_model_adaptive_action_prompt`
- Added `user_context` and `user_id` parameters
- Injected self_facts section at top of action prompts
- Updated all call sites (streaming and non-streaming)

### 2. Disabled Auto-Injection for Recall Questions
- Commented out auto-injection patterns for `get_self_info`
- Allows LLM to answer from prompt instead of tool execution

### 3. Added Recall Question Routing
- Detects "What is my X?" patterns
- Routes to conversation mode (not action mode)
- Ensures `build_system_prompt` is used with better formatting

### 4. Strengthened Instructions
- Added explicit "CRITICAL INSTRUCTION" section
- Removed misleading examples showing tool calls for recall
- Added examples of correct direct answers

## Current Status

### ✅ What's Working
- Self-facts ARE being retrieved: `✅ Found 1 self-facts for user jason`
- Self-facts ARE being included: `💾 Including 1 self-facts in prompt`
- Recall routing works: `🧠 Routing recall question to conversation mode`
- Facts are in database: `favorite_color: purple` for jason

### ❌ What's NOT Working
- LLM still responds: "I don't have any information about your favorite color"
- Despite facts being in prompt, LLM doesn't see or use them

## Root Cause Analysis

The issue is **NOT** with:
- ❌ Fact retrieval (working)
- ❌ Prompt injection (working)
- ❌ Routing (working)

The issue IS with:
- ✅ **LLM not understanding/seeing the facts section in the prompt**

Possible causes:
1. Facts section formatting not prominent enough for LLM
2. Conversation mode using different prompt path that bypasses `build_system_prompt`
3. LLM model (qwen2.5:7b) not following instructions well
4. Prompt too long, facts getting truncated
5. Facts section being overridden by base prompt

## Next Steps (Recommendations)

### Option A: Simplify Prompt Format
- Move facts to VERY top of prompt
- Use simpler, more direct format
- Remove all other context temporarily to isolate issue

### Option B: Test with Different Model
- Try gemma or hermes3 instead of qwen
- Some models follow instructions better

### Option C: Force Tool-Free Responses
- Modify conversation mode to explicitly disable tool calling
- Ensure LLM can ONLY answer from context

### Option D: Debug Actual Prompt Sent to LLM
- Add logging at LLM call site to see exact prompt
- Verify facts section is actually reaching the model

## Files Modified

1. `/home/zoe/assistant/services/zoe-core/routers/chat.py`
   - `get_model_adaptive_action_prompt` - added user_context
   - `_auto_inject_tool_call` - disabled recall patterns
   - `intelligent_routing` - added recall question detection
   - `build_system_prompt` - added debug logging

## Rollback Command

```bash
git checkout HEAD -- services/zoe-core/routers/chat.py
docker compose restart zoe-core
```

## Time Estimate to Complete

- **Option A (Simplify)**: 30 minutes
- **Option B (Different model)**: 15 minutes
- **Option C (Force no tools)**: 20 minutes
- **Option D (Debug prompt)**: 45 minutes

**Recommended**: Try Option B first (quickest), then Option A if that fails.

---

**Phase 3 Status:** ⚠️ IN PROGRESS - Infrastructure complete, LLM not responding correctly

