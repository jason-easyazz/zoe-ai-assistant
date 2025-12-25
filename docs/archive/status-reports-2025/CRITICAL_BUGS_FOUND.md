# CRITICAL BUGS FOUND - Test Results Analysis
**Date:** December 8, 2025  
**Test Pass Rate:** 15.4% (2/13 tests passed)  
**Status:** 🚨 **PRODUCTION BLOCKING ISSUES**

---

## 🔥 **CRITICAL BUG #1: Calendar Goes to Shopping List**

### The Problem
```
User: "Add dentist appointment tomorrow at 3pm"
Zoe: "✅ Added Dentist Appointment Tomorrow At 3Pm to your shopping list!"
```

**This is EXACTLY what Andrew reported!**

### Root Cause
The tool routing is sending calendar events to `add_to_list` instead of `create_calendar_event`.

### Evidence
- 4/4 calendar tests failed
- All calendar events went to shopping list or got confused
- Tool boundaries in prompt templates are being ignored

### Impact
**CRITICAL** - Users cannot add calendar events via natural language

---

## 🔥 **CRITICAL BUG #2: Self-Facts Not in Prompts**

### The Problem
```
Stored in DB: favorite_food = "Sushi"
User: "What's my favorite food?"
Zoe: "I'm not sure what your favorite food is."
```

### Root Cause
The code to include `self_facts` in the system prompt IS there, but it's not being executed. Either:
1. The user_context doesn't have self_facts when passed to build_system_prompt
2. The prompt is being cached without self_facts
3. The routing to "action" skips the self_facts section

### Evidence
- 5/5 recall tests failed
- Facts ARE in database ✅
- Facts NOT in responses ❌
- Debug logs show "Found 1 self-facts" but never "💾 Including X self-facts"

### Impact
**CRITICAL** - The entire memory system is non-functional for users

---

## ✅ What Actually Works

1. **Self-Facts Extraction** - New facts are stored correctly
2. **Shopping Lists** - All 3 tests passed
3. **People Recall** - Zoe knows about Sarah

---

## 🎯 Required Fixes (In Order of Priority)

### FIX #1: Calendar Tool Routing (30 mins)
**Problem:** Calendar events going to shopping list  
**Solution:** 
1. Check tool descriptions in prompt_templates.py
2. Verify routing logic in chat.py
3. Add explicit calendar keywords
4. Test with Andrew's exact phrasing

### FIX #2: Self-Facts in Prompts (1 hour)
**Problem:** Facts stored but not recalled  
**Solution:**
1. Debug why build_system_prompt isn't including self_facts
2. Check if user_context is passed correctly
3. Verify self_facts aren't filtered out somewhere
4. Force cache invalidation
5. Add self_facts to ALL prompt paths (greeting, action, conversation)

### FIX #3: Conversation Memory (Low Priority)
**Problem:** Zoe doesn't remember earlier in conversation  
**Impact:** Medium - Episode memory exists but needs tuning

---

## 📊 Test Results Summary

| Category | Passed | Failed | Pass Rate |
|----------|--------|--------|-----------|
| User Identity | 0 | 1 | 0% |
| Self-Facts Recall | 0 | 5 | 0% |
| Self-Facts Extraction | 1 | 0 | 100% |
| People Recall | 1 | 0 | 100% |
| Calendar NL | 0 | 4 | 0% |
| Shopping List NL | 3 | 0 | 100% |
| Conversation Memory | 0 | 1 | 0% |
| **TOTAL** | **2** | **11** | **15.4%** |

---

## 🚨 Bottom Line

**Your assessment was correct:** "Swing and a miss"

The comprehensive testing revealed:
- Only 15% of features work
- Calendar is completely broken (Andrew's complaint validated)
- Memory system stores but doesn't recall
- This is NOT production-ready

**Need immediate fixes to calendar routing and self-facts inclusion.**

Would you like me to:
1. Fix the calendar routing NOW
2. Fix the self-facts recall NOW
3. Both
4. Show you the exact bugs first for review

