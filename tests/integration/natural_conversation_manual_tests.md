# Natural Conversation Manual Test Scenarios

These scenarios should be tested manually via voice or web UI to verify human-like interaction.

## Scenario 1: Shopping List with Natural Corrections

**Test Flow:**
1. "Add milk to my shopping list"
2. "Actually, make that oat milk"  
3. "What did I just add?"
   - **Expected**: "oat milk" or "You just added oat milk"
4. "Remove it"
   - **Expected**: Removes oat milk

**Pass Criteria**: Zoe understands corrections and pronoun references naturally

---

## Scenario 2: Multi-Turn Event Planning

**Test Flow:**
1. "I need to schedule a dentist appointment"
2. "Next Tuesday works for me"
3. "Make it at 2pm"
4. "Actually, 3pm would be better"
5. "What time is that appointment?"
   - **Expected**: "3pm" or "Your dentist appointment is at 3pm next Tuesday"

**Pass Criteria**: Zoe maintains conversation context across 5+ turns

---

## Scenario 3: Complex Multi-System Request

**Test Flow:**
1. "Plan my morning tomorrow"
   - **Expected**: Shows calendar + suggests tasks from lists + checks for birthdays
2. "Add coffee to shopping list"
3. "What did we just plan?"
   - **Expected**: Summarizes morning plan

**Pass Criteria**: Orchestration works + conversational memory of the plan

---

## Scenario 4: Conversational Repair

**Test Flow:**
1. "Remind me to call Mom"
2. "Wait, I meant Dad not Mom"
3. "When am I calling him?"
   - **Expected**: "Dad" (not Mom)

**Pass Criteria**: Corrections override previous info + pronouns resolve correctly

---

## Scenario 5: Temporal References

**Test Flow:**
1. "My sister Sarah's birthday is June 15th"
2. Wait 30 seconds
3. "What did I tell you a minute ago?"
   - **Expected**: Recalls Sarah's birthday information
4. "Remind me a week before that"
   - **Expected**: Creates reminder for June 8th

**Pass Criteria**: Temporal memory + relative time calculations work

---

## Scenario 6: Implicit Context Continuation

**Test Flow:**
1. "Show me my calendar for tomorrow"
2. "Any meetings?" (implicit: "on tomorrow's calendar")
3. "Free time?" (implicit: "when do I have free time tomorrow")

**Pass Criteria**: Zoe understands implicit subject continuation

---

## Scenario 7: Pronoun Chain Resolution

**Test Flow:**
1. "Tell me about the Smith project"
2. "Who's working on it?"
3. "When did they start?"
4. "What's their deadline?"

**Pass Criteria**: Zoe maintains pronoun reference chain (it → project, they → team)

---

## Scenario 8: Negative Clarification

**Test Flow:**
1. "Do I have meetings today?"
2. If answer is "No" or "No meetings": "What about tomorrow?"
3. If meetings exist: "Can you move them?"

**Pass Criteria**: Handles negative responses naturally and continues conversation

---

## Scenario 9: Multi-Step Task with Dependencies

**Test Flow:**
1. "I need to prepare for the presentation on Friday"
2. "Find my notes about the topic"
3. "Add practice time to my calendar"
4. "Remind me to print handouts the day before"

**Pass Criteria**: Zoe orchestrates multiple systems based on one goal

---

## Scenario 10: Conversational Backtracking

**Test Flow:**
1. "Add milk to shopping"
2. "Also bread"
3. "Actually, forget the milk"
4. "What's on my list now?"
   - **Expected**: Only bread (milk was removed)

**Pass Criteria**: Handles backtracking and undoing actions

---

## Performance Test

**Test**: Have a 10-turn conversation at normal speaking pace

**Sample Conversation**:
1. "What's my schedule today?"
2. "Add coffee to shopping"
3. "When is the team meeting?"
4. "Who's attending?"
5. "Send them a reminder"
6. "What did I add to the list?"
7. "Make it two packs"
8. "What time is the meeting again?"
9. "Move it to 3pm"
10. "Confirm the changes"

**Expected**: Zero lag, <3s response time per turn (with Jetson)  
**Pass Criteria**: Feels like talking to a person, not waiting for a computer

---

## Edge Case Testing

### Ambiguous Pronouns
- "John and Sarah are coming. He's bringing wine." (He = John)
- Test if Zoe resolves correctly or asks for clarification

### Time Ambiguity
- "Schedule meeting tomorrow" (what time?)
- "Remind me next week" (which day?)
- Zoe should ask clarifying questions

### Interruption Handling
- Mid-sentence: "Add mil— actually never mind"
- Zoe should handle gracefully

### Multiple Items
- "Add eggs, milk, bread, cheese, and butter to shopping"
- All 5 items should be added

---

## Voice-Specific Tests

### Pronunciation Variations
- "Schedule" vs "shed-yule"
- "February" variations
- Test Whisper STT handles accents

### Noise Handling
- Test with background noise
- Test with music playing
- Verify transcription accuracy

### Natural Pauses
- Test with "um", "uh", "like" fillers
- Zoe should ignore or handle naturally

---

## Success Metrics

**Before Jetson (Current Pi 5)**:
- [ ] 80%+ scenarios pass
- [ ] <10s response time average
- [ ] Temporal memory works 3+ turns
- [ ] Orchestration triggers correctly

**After Jetson Upgrade**:
- [ ] 95%+ scenarios pass
- [ ] <3s response time average
- [ ] Zero perceived lag in voice
- [ ] Real-time conversation flow

---

## Testing Checklist

### Pre-Test Setup
- [ ] Zoe services running (`./start-zoe.sh`)
- [ ] Database optimized (indexes added)
- [ ] Voice system tested (mic working)
- [ ] Test user created

### During Testing
- [ ] Record response times
- [ ] Note any errors or failures
- [ ] Document unexpected behaviors
- [ ] Test both voice and text UI

### Post-Test
- [ ] Review satisfaction metrics
- [ ] Check temporal memory database
- [ ] Analyze performance logs
- [ ] Document improvements needed

---

## Reporting Template

```
Scenario: [Name]
Date: [YYYY-MM-DD]
Tester: [Name]
Platform: [Voice/Web/Both]

Results:
- Pass/Fail: 
- Response Time: 
- Notes: 

Issues Found:
1. 
2. 

Recommendations:
1. 
2. 
```

---

**Last Updated**: October 13, 2025  
**Status**: Ready for testing after Phase 1-6 implementation  
**Next**: Run automated tests, then manual scenarios



