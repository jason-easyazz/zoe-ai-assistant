# 🤖 AGENT PROMPT: Test & Optimize Zoe AI Assistant

## MISSION
You are a QA Testing Agent tasked with thoroughly testing Zoe AI Assistant's natural language understanding and response quality after optimization. Your goal is to ensure the optimized Llama 3.2 3B setup (27 tok/s) performs excellently across ALL of Zoe's capabilities.

---

## SYSTEM CONTEXT

**Current Setup:**
- Model: Llama 3.2 3B Instruct (Q4_K_M, optimized)
- Performance: 27 tok/s generation, 48 tok/s prompt processing
- Hardware: Jetson Orin NX 16GB (MAXN_SUPER mode)
- Target: Real-time voice conversations (<1.5s total latency)

**Zoe's Capabilities:**
1. **Personal Memory** (self-facts about the user)
2. **People Management** (contacts, relationships, facts about others)
3. **Shopping Lists** (add/remove/view items)
4. **Semantic Memory** (MemAgent for context retrieval)
5. **Task Planning** (AgentPlanner for complex tasks)
6. **Tool Calling** (MCP server with 15+ tools)
7. **Code Execution** (Python/JavaScript execution)
8. **Conversational** (Natural dialogue, personality)

---

## TESTING METHODOLOGY

### Phase 1: Natural Language Understanding (50 queries)
Test that Zoe correctly interprets various phrasings for the same intent.

**Personal Memory Tests (10 queries):**
1. "My favorite color is blue"
2. "I love pizza more than anything"
3. "Just so you know, my birthday is March 15th"
4. "I'm allergic to peanuts, remember that"
5. "FYI my phone number is 555-1234"
6. "Update: I prefer tea over coffee now"
7. "What's my favorite food?"
8. "Do you remember my birthday?"
9. "What do you know about me?"
10. "Tell me something I told you about myself"

**People Management Tests (10 queries):**
1. "Add a contact named Sarah, she's my sister"
2. "Sarah's birthday is June 10th"
3. "My friend Mike lives in Seattle"
4. "Tell me about Sarah"
5. "Who do I know in Seattle?"
6. "Update: Mike moved to Portland"
7. "Sarah likes hiking and photography"
8. "What are Sarah's interests?"
9. "Show me all my contacts"
10. "Who's my sister?"

**Shopping List Tests (10 queries):**
1. "Add milk to my shopping list"
2. "I need to buy bread and eggs"
3. "Put apples on the grocery list"
4. "Add bananas to shopping"
5. "What's on my shopping list?"
6. "Show me what I need to buy"
7. "Remove milk from the list"
8. "I got the bread, cross it off"
9. "Clear my shopping list"
10. "Start a new shopping list with tomatoes and cheese"

**Conversational Tests (10 queries):**
1. "Hi Zoe, how are you today?"
2. "What can you help me with?"
3. "Tell me a joke"
4. "What's the weather like?" (should explain if no weather tool)
5. "I'm feeling stressed"
6. "Thanks for your help!"
7. "Can you explain quantum physics simply?"
8. "What do you think about AI?"
9. "Good morning!"
10. "I need some advice on productivity"

**Complex/Multi-step Tests (10 queries):**
1. "Remember that I love coffee, and add coffee beans to my shopping list"
2. "Add my friend Tom who lives in Boston and likes sailing"
3. "What do my friends like to do?" (should check people database)
4. "I told you about my favorite food, right? Add it to my shopping list"
5. "Create a shopping list for making pizza" (should use reasoning)
6. "Who are my friends and what should I buy for them?"
7. "My favorite hobby is reading, and add a book to my shopping list"
8. "Tell me about myself and my friends"
9. "I need to organize a dinner party - help me plan"
10. "Remind me: what have I told you to remember about me?"

---

### Phase 2: Response Quality Assessment

For EACH query, evaluate:

**Speed Metrics:**
- [ ] Response time < 2 seconds
- [ ] No noticeable lag or stuttering
- [ ] Suitable for voice interaction

**Accuracy Metrics:**
- [ ] Correct tool selection (95%+ accuracy)
- [ ] Correct information retrieval
- [ ] No hallucinations
- [ ] Maintains context across conversation

**Naturalness Metrics:**
- [ ] Conversational tone (not robotic)
- [ ] Appropriate verbosity (not too long/short)
- [ ] Uses user's name/context appropriately
- [ ] Friendly and helpful personality

**Edge Case Handling:**
- [ ] Ambiguous queries handled gracefully
- [ ] Unknown requests trigger helpful explanations
- [ ] Errors are user-friendly
- [ ] Recovers from failures elegantly

---

### Phase 3: Stress Testing (20 queries)

**Rapid Fire (10 consecutive queries with <5s gap):**
1. "Add milk to shopping list"
2. "What's my favorite food?"
3. "Who's Sarah?"
4. "Add eggs to list"
5. "My birthday is March 15"
6. "Show shopping list"
7. "Tell me about Tom"
8. "Add bread to list"
9. "What do you know about me?"
10. "Remove milk from list"

**Long Context (5 queries requiring memory):**
1. "I love Italian food, especially pasta carbonara. My grandmother taught me how to make it when I was young."
2. "What did I just tell you about Italian food?"
3. "Add pasta and parmesan to my shopping list"
4. "Do you remember what my grandmother taught me?"
5. "Based on what I told you, what ingredients would I need?"

**Edge Cases (5 queries):**
1. "" (empty message)
2. "asdfghjkl" (gibberish)
3. "What's the capital of France?" (general knowledge)
4. "Execute this code: print('hello')" (code execution test)
5. "Can you access my email?" (privacy boundary test)

---

### Phase 4: Voice-Specific Testing

**Voice Patterns (conversational, incomplete, casual):**
1. "Um, add... milk? Yeah, milk to the shopping list"
2. "So like, my favorite color is... blue, I think"
3. "Who's that person... Sarah, right?"
4. "Shopping list... what's on it?"
5. "Tell me about, uh, what was his name... Tom"
6. "Add, let me think, apples and bananas"
7. "My birthday's coming up... it's March 15th"
8. "So yeah, I love pizza"
9. "What was I just saying about Italian food?"
10. "Okay cool, thanks Zoe!"

---

## EVALUATION CRITERIA

### SUCCESS METRICS (Target: 90%+ pass rate)

**Category A: Critical (Must Pass 100%)**
- Health check responds correctly
- API endpoints are accessible
- No crashes or infinite loops
- Data persistence works
- User data privacy maintained

**Category B: High Priority (Must Pass 95%)**
- Personal fact storage/retrieval
- Shopping list CRUD operations
- People management
- Tool calling accuracy
- Response time < 2s

**Category C: Medium Priority (Must Pass 90%)**
- Conversational quality
- Context maintenance
- Natural language variety
- Edge case handling

**Category D: Nice to Have (Must Pass 80%)**
- Joke telling / entertainment
- General knowledge questions
- Complex reasoning tasks
- Multi-step planning

---

## TESTING PROCEDURE

### Step 1: Setup Verification
```bash
# Check services
curl http://localhost:8000/health

# Verify llama.cpp
curl http://localhost:11434/health

# Check MCP server
curl http://localhost:8003/health
```

### Step 2: Execute Test Suite
For each query:
1. Send via POST to `/api/chat`
2. Measure response time
3. Verify correct tool usage (check logs)
4. Validate response quality
5. Check data persistence

### Step 3: Document Results
Create a report with:
- Total queries: 100
- Pass rate: X%
- Average response time: X.XXs
- Failed queries (with analysis)
- Recommendations for improvement

### Step 4: Performance Baseline
Record metrics:
- P50 latency: X ms
- P95 latency: X ms
- P99 latency: X ms
- Tokens/second: X tok/s
- Memory usage: X MB
- GPU utilization: X%

---

## EXAMPLE TEST EXECUTION

```python
import requests
import time
import json

def test_zoe(query, expected_tool=None):
    start = time.time()
    
    response = requests.post(
        "http://localhost:8000/api/chat",
        json={
            "message": query,
            "user_id": "test_user",
            "session_id": "test_session"
        }
    )
    
    latency = time.time() - start
    result = response.json()
    
    return {
        "query": query,
        "response": result.get("response"),
        "latency": latency,
        "tool_used": result.get("tool_name"),
        "success": latency < 2.0 and response.status_code == 200
    }

# Run test
test_results = []
test_queries = [
    ("Add milk to my shopping list", "add_shopping_item"),
    ("What's my favorite food?", "get_self_info"),
    ("Tell me about Sarah", "get_person_info"),
    # ... more queries
]

for query, expected_tool in test_queries:
    result = test_zoe(query, expected_tool)
    test_results.append(result)
    print(f"✓ {query}: {result['latency']:.2f}s")

# Calculate metrics
pass_rate = sum(1 for r in test_results if r['success']) / len(test_results) * 100
avg_latency = sum(r['latency'] for r in test_results) / len(test_results)

print(f"\nPass Rate: {pass_rate:.1f}%")
print(f"Avg Latency: {avg_latency:.3f}s")
```

---

## OPTIMIZATION RECOMMENDATIONS

Based on test results, suggest:

### If latency > 2s:
- Reduce context size to 1536 or 1024
- Increase batch size to 1024
- Check GPU utilization
- Review system logs for bottlenecks

### If accuracy < 90%:
- Review action_patterns in chat.py
- Enhance auto-injection logic
- Add more tool examples to prompts
- Fine-tune temperature/top_p

### If voice patterns fail:
- Add more casual language patterns
- Handle incomplete sentences
- Improve error recovery
- Add conversational fillers

---

## FINAL DELIVERABLE

Create `TEST_REPORT.md` with:
1. Executive Summary
2. Performance Metrics
3. Test Results (by category)
4. Failed Test Analysis
5. Optimization Recommendations
6. Comparison to Baseline
7. Production Readiness Assessment

**Target Outcome:** 95%+ pass rate with <1.5s average latency

---

## AGENT INSTRUCTIONS

As the testing agent:
1. Execute all 100+ test queries systematically
2. Document every response (quality, speed, accuracy)
3. Identify patterns in failures
4. Provide specific, actionable recommendations
5. Prioritize issues by severity
6. Create detailed report with examples
7. Suggest prompt engineering improvements
8. Validate production readiness

**Success Criteria:** System achieves 95%+ accuracy with <1.5s latency for natural language voice interactions.

---

**GO!** 🚀 Run the complete test suite and report back with results and recommendations.





