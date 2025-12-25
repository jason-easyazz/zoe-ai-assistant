# Zoe User Memory & Onboarding - Status Report
**Date:** December 8, 2025  
**Assessment:** Mixed Results - Core Infrastructure Works, User Experience Gaps Remain

---

## 🎯 What Was Requested

1. **User Memory System**: Zoe should remember things users tell her
2. **User Onboarding**: Conversational flow asking questions to populate user profiles
3. **User Identity**: Zoe should know WHO she's talking to
4. **Data Persistence**: All chats, facts, and user data should be stored

---

## ✅ What's Working

### 1. Chat Message Persistence ✅
- **Status**: WORKING
- **Evidence**: 47 messages stored in database
- **Implementation**: Phase 1 complete - `save_chat_message()` saves all user and assistant messages
- **Test Result**: Messages are being saved successfully

### 2. User Identity in Greetings ✅
- **Status**: WORKING
- **Evidence**: User said "hi", Zoe responded "Hi **Jason**, how's your day going so far?"
- **Implementation**: Greeting prompts include user name from database
- **Test Result**: User is correctly identified in greeting context

### 3. Self-Facts Table Structure ✅
- **Status**: EXISTS
- **Schema**: `user_id, fact_key, fact_value, confidence, source, created_at, updated_at`
- **Implementation**: Table created and accessible

### 4. Session History UI ✅
- **Status**: LOADED
- **Evidence**: Browser logs show "📋 Loaded sessions: 4 (4)"
- **Implementation**: chat-sessions.js v2.1 working with apiRequest()

---

## ❌ What's NOT Working

### 1. User Identity in Main Conversation ❌
- **Status**: BROKEN
- **Evidence**: User asked "what's my name", Zoe said "I don't have any information about your name"
- **Root Cause**: System prompt cache key changed to `conversation_v2` but old cache persists
- **Impact**: HIGH - Zoe doesn't know user's name in normal conversation

### 2. Self-Facts Auto-Extraction ❌
- **Status**: PARTIALLY BROKEN
- **Evidence**: User said "My favourite colour is black", but it wasn't stored
- **Root Cause**: Pattern only matches "color" not "colour" (British spelling)
- **Current Data**: Only 1 fact in database (from user "andrew", not "jason")
- **Impact**: HIGH - Memory system not learning from conversations

### 3. Self-Facts Recall in Responses ✅ then ❌
- **First Test**: User said "my favourite colour is black", later asked "what's my favourite colour"
- **Zoe Response**: "You mentioned earlier that your favourite colour is black" ✅
- **BUT**: This was from short-term memory (episode context), NOT from self_facts table
- **Impact**: Memory only lasts for current session, not persistent

### 4. Onboarding System ❌
- **Status**: NOT INTEGRATED
- **Evidence**: No onboarding records in database
- **Root Cause**: 
  - `/api/onboarding` router exists but has syntax errors (lines 361, 381, 434)
  - Not integrated into chat flow - users don't know it exists
  - No UI trigger to start onboarding
- **Impact**: CRITICAL - The entire onboarding questionnaire never executes

---

## 🔍 Technical Issues Identified

### Issue #1: Prompt Cache Not Clearing
**Problem**: Changed cache key to `conversation_v2_{user_id}` but Redis cache persists between restarts  
**Evidence**: Logs show `✅ Cached conversation prompt (streaming) v2`  
**Solution Attempted**: Restarted zoe-core, cleared Redis with FLUSHALL  
**Status**: Still caching old prompts without user identity

### Issue #2: Self-Facts Pattern Matching Too Rigid
**Problem**: Patterns like `r"my favorite (\w+) is (.+?)"` don't handle:
- British spelling (colour vs color)
- Variations ("favourite" vs "favorite")  
- Complex sentences
- Typos or natural language variations

**Current Patterns**:
```python
r"my favorite (\w+) is (.+?)(?:\.|$|,)"  # US spelling only
r"my name is (.+?)(?:\.|$|,)"
r"i live in (.+?)(?:\.|$|,)"
```

**Missing**:
- British spellings
- Informal language ("i like X", "X is my favorite")
- Multi-word attributes ("favorite ice cream flavor")

### Issue #3: Onboarding Router Has Syntax Errors
**File**: `/home/zoe/assistant/services/zoe-core/routers/onboarding.py`

**Errors**:
- Line 361: `async def start_onboarding(session: AuthenticatedSession = Depends(validate_session)):` - Missing `user_id` parameter extraction
- Line 381: `user_id = session.user_id` appears randomly in SQL string
- Line 434: Similar issue with session parameter

**Missing**:
- Import for `Depends`, `AuthenticatedSession`, `validate_session`
- Integration with chat.py to trigger onboarding
- UI button/link to start onboarding

### Issue #4: User Identity Not in Build Prompt Logs
**Expected**: `🆔 Building prompt for user_id='jason' → name='Jason'`  
**Actual**: Log line never appears  
**Reason**: Using cached prompt, `build_system_prompt()` never called

---

## 📊 Database Audit Results

```
Chat Messages: 47 (✅ Working)
Self Facts: 1 (❌ Broken - should have "favorite_colour: black" for jason)
Onboarding Records: 0 (❌ Never started)
Registered Users: 3 (jason, andrew, teneeka)
```

---

## 🚨 Critical Path to Fix

### Priority 1: User Identity in Conversation (BLOCKER)
**Why**: Without this, Zoe doesn't know who she's talking to  
**Actions**:
1. Clear Redis cache completely: `docker exec zoe-redis redis-cli FLUSHDB`
2. Restart zoe-core: `docker restart zoe-core`
3. Test: Ask "what's my name" → Should say "Jason"
4. Verify logs show: `🆔 Building prompt for user_id='jason'`

### Priority 2: Fix Self-Facts Extraction (HIGH)
**Why**: Memory system is useless if it doesn't remember anything  
**Actions**:
1. Improve regex patterns to handle variations
2. Add fuzzy matching for common patterns
3. Consider using LLM to extract facts instead of regex
4. Test: "My favourite colour is black" → Should store `favorite_colour: black`

### Priority 3: Integrate Onboarding (HIGH)
**Why**: This was specifically requested and never worked  
**Actions**:
1. Fix syntax errors in `onboarding.py`
2. Add endpoint to detect first-time users
3. Trigger onboarding automatically on first chat
4. Add "Start Onboarding" button in UI (people.html or profile section)
5. Test complete flow from start to finish

### Priority 4: Make Self-Facts Available to LLM (MEDIUM)
**Why**: Storing facts is useless if Zoe doesn't use them  
**Actions**:
1. Modify `get_user_context()` to query self_facts table
2. Include self_facts in memory retrieval
3. Format facts prominently in system prompt
4. Test: After storing "favorite_colour: black", ask "what's my favorite colour" → Should recall from database

---

## 🎯 Success Criteria (How to Know It's Fixed)

| Test | Expected Result | Current Status |
|------|----------------|----------------|
| "What's my name?" | "Your name is Jason" | ❌ FAIL |
| "My favorite color is blue" | Stores `favorite_color: blue` | ❌ FAIL |
| Ask "what's my favorite color" | "Your favorite color is blue" | ❌ FAIL (uses episode, not database) |
| New user login → Chat | Onboarding questionnaire starts | ❌ NEVER IMPLEMENTED |
| Click "View Profile" | Shows stored facts about user | ❌ NO UI |
| Widget/Orb chat appears in history | Session sidebar shows all chats | ✅ PASS |

---

## 💡 Recommended Implementation Order

1. **Fix user identity in conversation** (30 min)
   - Force cache clear
   - Verify logging
   - Test with "what's my name"

2. **Improve self-facts extraction** (2 hours)
   - Better regex patterns
   - Add British spelling support
   - Consider LLM-based extraction
   - Add extensive testing

3. **Fix and integrate onboarding** (4 hours)
   - Fix syntax errors
   - Add auth integration
   - Create trigger logic
   - Build UI components
   - Test end-to-end

4. **Add self-facts to LLM context** (1 hour)
   - Query self_facts in `get_user_context()`
   - Format for prompt inclusion
   - Test recall accuracy

5. **Build profile UI** (2 hours)
   - Display all user facts
   - Show onboarding progress
   - Allow editing/deleting facts

---

## 📝 User's Accurate Assessment

> "it seems like a swing and a miss"

**Status**: ACCURATE

**Why**:
- Core infrastructure is there (tables, functions, logging)
- But user experience is broken (identity doesn't work, facts aren't extracted, onboarding never runs)
- It's like building a car with an engine, wheels, and steering wheel - but forgetting to connect the steering wheel to the wheels

**What Needs to Happen**:
- Clear the cache ONE MORE TIME (properly this time)
- Fix the self-facts patterns to actually catch user statements
- Wire up the onboarding system so it actually runs
- Test everything end-to-end from a user's perspective

**Bottom Line**: The code is 80% there, but the last 20% (making it actually work for users) is missing.

