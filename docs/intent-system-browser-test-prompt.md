# Intent System Browser Test — New Chat Prompt

## Paste this into a new Cursor chat:

```
I want you to do a deep browser-based QA test of Zoe's intent system, agents, 
and chat interface. Use the Cursor browser tool to navigate to Zoe's UI and 
run through a full test suite.

## Zoe's URL
http://localhost (or https://zoe.local if on local network)
Touch panel: http://localhost/touch (or /touch.html)

## What was built today (what to test)

### 1. ConversationContext — coreference resolution
After a volume command succeeds, follow up with short context-dependent phrases.
These should now work WITHOUT needing to say "volume" again.

### 2. Voice history gap fix
Voice LLM fallback now has last 3 turns of context — test multi-turn voice-style 
chat where each message references something from the previous reply.

### 3. Tier 0.5 LLM classifier
Short utterances that don't match regex but are clearly intentful should now 
route correctly instead of confusing Zoe or requiring login.

### 4. Pi Agent → Zoe Agent rename
Nothing user-visible, but confirm logs don't show old "pi_agent:" prefix.

### 5. Weather card on touch panel
Was broken (double ? in URL, missing ZoeVoiceCard, toast bug). Should now 
load correctly.

---

## Test Script (run these IN ORDER in the chat interface at localhost)

### BLOCK A — Basic intent fast path (should be instant, <1s)
1. "What time is it?"
2. "Hello"
3. "Hi Zoe"
4. "What's 25 times 48?"
5. "What's the weather like?"

### BLOCK B — Volume coreference (THE key scenario that was failing)
6. "Set the volume to 50%"
   → Zoe should confirm "Volume set to 50%"
7. "Make it 80"  
   → Should resolve from context → "Volume set to 80%" (NOT blocked, NOT music)
8. "Turn it down a bit"
   → Should resolve direction from context → volume decreases
9. "65 percent"
   → Should set volume to 65 from context (short form, no "volume" word)

### BLOCK C — Music coreference
10. "Play some music"
    → Music should start
11. "Make it louder"
    → Volume should increase (music context coreference)
12. "80%"
    → Should set music volume to 80 (not blocked)

### BLOCK D — Smart home
13. "Turn on the kitchen light"
14. "Turn it off"  ← coreference test (should turn off kitchen light)
15. "Dim the lights to 50%"

### BLOCK E — Multi-turn context (voice history gap test)
16. "What's on my calendar today?"
    → Note what Zoe says
17. "What about tomorrow?"
    → Should understand this means "calendar for tomorrow" not "what about weather tomorrow"
18. "Add a meeting at 3pm"
    → Should add to calendar (not confused about context)

### BLOCK F — Sentences the old system was failing on
19. "you make it 80" (STT artifact — "you" prepended)
    → Should set volume to 80, NOT be blocked
20. "turn it up to 75%" 
    → Should set volume, NOT go to music player
21. "a bit quieter"
    → Should lower volume from context
22. "louder please"
    → Should raise volume

### BLOCK G — Things that should go to the LLM (not intent router)
23. "Tell me something interesting about octopuses"
24. "Write me a haiku about rain"
25. "What's the capital of Mongolia?"
    → These should get smart LLM answers, not "I don't understand"

### BLOCK H — Guest/auth (touch panel)
Navigate to the touch panel (/touch or /touch.html) and try:
26. "Volume up"  → should work without login
27. "Turn on the lights"  → should work without login  
28. "Make it 80"  → should NOT say "blocked for this role"

### BLOCK I — Weather card on touch panel
29. Navigate to the touch panel weather view directly
    → Should load weather data without infinite redirect loop
    → Should NOT show "could not load weather" 

### BLOCK J — Escalation to OpenClaw (should work but take longer)
30. "Search the web for the latest news about AI assistants"
31. "Set up a new automation in Home Assistant for me"

---

## What to look for / report

For EACH test:
- Did it work as expected? (Y/N)
- Response time (instant/fast/slow/timeout)
- Exact response text (especially for failures)
- Any "blocked for this role" or auth errors
- Any 500 errors in the browser console (F12)

**Special attention:**
- Tests 7, 8, 9, 12, 19, 20, 21, 22 — these are the COREFERENCE tests. 
  They MUST work now. If any fail, check docker logs for the intent that fired.
- Tests 26, 27, 28 on the touch panel — must NOT require login
- Test 29 — weather card must load cleanly

## How to check logs during testing
In a terminal on the Jetson:
```bash
docker logs zoe-data -f 2>&1 | grep -E "intent|zoe_agent|Tier|coreference|volume|ERROR" 
```
Or check the log at: http://localhost/api/system/logs (if that endpoint exists)

## After testing — report
- Overall pass rate (X/31 tests passed)
- List of any failures with exact error text
- Suggested fixes for anything still broken
- Check browser console for JS errors on the touch panel weather page
```
