# Zoe Memory & Tool System Fix - Implementation Progress

## Phase 0: Data Audit ✅ COMPLETE

**Duration:** 30 minutes

**Findings:**
- `self_facts` table: 11 facts across 3 users (jason, demo_test_user, andrew)
- `people.is_self` records: 4 users, ALL with empty JSON (no migration needed)
- Primary data source: `self_facts` table
- Schema validation: All columns match code expectations

**Decision:** Simplified Phase 1 - query self_facts as primary, people.is_self as fallback

---

## Phase 1: Merge Dual Memory Systems ✅ FUNCTIONALLY COMPLETE

**Duration:** 1.5 hours (longer than expected due to user_id extraction debugging)

**Changes Made:**

1. **`services/zoe-mcp-server/main.py`** - `_get_self_info` method
   - Queries BOTH `self_facts` and `people.is_self` tables
   - Defensive JSON parsing (handles null, malformed data)
   - Suffix matching for fact keys ("color" matches "favorite_color")
   - Fixed user_id extraction from args

2. **`services/zoe-mcp-server/http_mcp_server.py`**
   - Added `user_id` field to `ToolRequest` base class
   - Fixed endpoint to extract user_id correctly
   - Added debug logging

**Test Results:**

✅ **Direct tool calls work perfectly:**
```bash
curl http://localhost:8003/tools/get_self_info -d '{"user_id": "jason"}'
→ "About User_jason: favorite color: purple"

curl http://localhost:8003/tools/get_self_info -d '{"user_id": "demo_test_user"}'
→ "About User: name: Alex Thompson, favorite color: Blue, pet: Golden Retriever..."
```

⚠️ **Chat interface issue:**
```
User: "What is my favorite color?"
Tool executes: get_self_info → "Your favorite color is purple"
Chat router: Replaces with "Executed get_self_info successfully" 
LLM sees: Generic success message (not actual data)
LLM responds: "I'm not sure what your favorite color is"
```

**Root Cause:** Chat router's tool execution logic replaces tool responses with generic messages instead of passing actual data to LLM.

**Solution:** Phase 3 will include self_facts directly in system prompt, bypassing tool execution flow entirely.

---

## Phase 2: Deterministic Tool Routing ⏳ NEXT

**Status:** Ready to implement

**Goal:** Force calendar vs shopping list tool selection for high-confidence patterns

**Approach:** Pre-select tool based on keywords, but still let LLM handle parameter extraction

**Implementation Plan:**

1. Add `deterministic_tool_selection()` function before `_chat_handler`
2. Check message for calendar patterns (appointment + time)
3. Check message for shopping patterns (add + list)
4. If match: Inject tool hint into routing, LLM still parses parameters
5. If no match: Normal LLM-based routing

**Files to modify:**
- `services/zoe-core/routers/chat.py` (add function + integrate)

**Time estimate:** 2 hours

---

## Phase 3: Self-Facts in All Prompts ⏳ PENDING

**Status:** Blocked on Phase 1 chat integration issue

**Goal:** Include self_facts in system prompt regardless of routing type

**Why this fixes Phase 1:** LLM will have direct access to facts in prompt, doesn't need tool execution

**Files to modify:**
- `services/zoe-core/routers/chat.py` - `build_system_prompt` function

**Time estimate:** 1 hour

---

## Current Status Summary

| Phase | Status | Pass Rate | Blocker |
|-------|--------|-----------|---------|
| 0: Data Audit | ✅ Complete | 100% | None |
| 1: Memory Merge | ⚠️ Tool works, chat broken | 50% | Tool response not reaching LLM |
| 2: Tool Routing | ⏳ Ready | 0% | None |
| 3: Facts in Prompts | ⏳ Pending | 0% | Phase 1 chat issue |

---

## Recommended Next Steps

**Option A: Continue with Phase 2 (Recommended)**
- Phase 2 is independent of Phase 1's chat issue
- Fixes Andrew's calendar routing problem immediately
- 2 hours of work
- High impact (calendar vs shopping accuracy)

**Option B: Fix Phase 1 chat integration first**
- Debug why tool responses are replaced with generic messages
- Requires deep dive into chat router tool execution flow
- Unknown time (could be 1-3 hours)
- Would unblock Phase 3

**Option C: Skip to Phase 3**
- Includes facts in prompt (fixes recall)
- Bypasses Phase 1's tool execution issue
- 1 hour of work
- Doesn't fix calendar routing (still need Phase 2)

---

## My Recommendation

**Proceed with Phase 2** because:
1. It's independent and ready to implement
2. Fixes a critical user-reported issue (Andrew's calendar problem)
3. High confidence in success (deterministic logic)
4. Phase 3 will fix Phase 1's recall issue anyway

After Phase 2, implement Phase 3, which will make Phase 1's tool execution issue irrelevant.

---

## Files Modified So Far

1. `/home/zoe/assistant/services/zoe-mcp-server/main.py` (Phase 1)
2. `/home/zoe/assistant/services/zoe-mcp-server/http_mcp_server.py` (Phase 1)

## Rollback Commands

```bash
# Phase 1 rollback
git checkout HEAD -- services/zoe-mcp-server/main.py services/zoe-mcp-server/http_mcp_server.py
docker compose restart zoe-mcp-server
```

---

**Total time invested:** 2 hours  
**Estimated remaining:** 3-4 hours (Phases 2-3)  
**Expected completion:** Day 1 end (if continuing)

