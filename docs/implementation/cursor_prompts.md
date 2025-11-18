# Cursor Prompt Templates

These templates help maintain consistency when implementing the Memory & Hallucination Reduction plan with Cursor.

---

## Template 1: Feature Implementation

Use this when starting implementation of a new feature.

```
I'm implementing [Feature Name] from Day [X] of the plan.

Context:
- Phase: [Phase Name]
- Files to create: [List from plan]
- Files to update: [List from plan]
- Feature flag: [Flag name] = false (default)

Requirements from plan:
[Copy relevant section from memory-enhancement.plan.md]

Please:
1. Read existing files to understand integration points
2. Implement the feature with complete code
3. Add appropriate error handling
4. Include logging statements
5. Create test cases
6. Provide validation commands

Follow master instructions for:
- File locations (PROJECT_STRUCTURE_RULES.md)
- Platform awareness (Jetson vs Pi 5)
- No duplicate files
```

### Example Usage

```
I'm implementing Context Validation from Day 8-10 of the plan.

Context:
- Phase: P0 Quick Wins
- Files to create: 
  - services/zoe-core/intent_system/validation/__init__.py
  - services/zoe-core/intent_system/validation/context_validator.py
- Files to update:
  - services/zoe-core/routers/chat.py
- Feature flag: USE_CONTEXT_VALIDATION = false (default)

Requirements from plan:
Skip context retrieval for deterministic Tier 0 intents unless they need data.
Add complexity heuristic: queries > 15 words or multiple questions need context.
Target: Tier 0 latency < 10ms (from ~200-500ms).

Please:
1. Read chat.py to understand current context fetching
2. Implement ContextValidator with should_retrieve_context() method
3. Add error handling for edge cases
4. Include logging for skipped context
5. Create test cases for Tier 0/1/2 intents
6. Provide validation commands to measure latency

Follow master instructions for file locations and no duplicate files.
```

---

## Template 2: Debugging

Use this when troubleshooting an issue.

```
I'm debugging [Issue] in [Feature].

Current behavior: [What's happening]
Expected behavior: [What should happen]
Relevant files: [List]
Error messages: [If any]

Context:
- Feature flag status: [enabled/disabled]
- Platform: [Jetson/Pi 5/Both]
- Related phase: [Phase Name]

Please:
1. Read the relevant files
2. Identify the root cause
3. Propose a fix with code
4. Explain why the issue occurred
5. Suggest how to prevent similar issues
6. Provide testing commands to verify the fix
```

### Example Usage

```
I'm debugging grounding checks not working in P0-4.

Current behavior: Grounding validator logs show 0% hallucination catches
Expected behavior: Should catch 30%+ of hallucinations
Relevant files:
- services/zoe-core/grounding_validator.py
- services/zoe-core/routers/chat.py
Error messages: None, just unexpectedly low catch rate

Context:
- Feature flag: USE_GROUNDING_CHECKS = true
- Platform: Jetson (async LLM method)
- Related phase: P0-4 (Days 16-19)

Please:
1. Read grounding_validator.py and chat.py integration
2. Identify why hallucinations aren't being caught
3. Propose a fix (threshold too high? wrong similarity metric?)
4. Explain the root cause
5. Suggest validation improvements
6. Provide commands to test catch rate
```

---

## Template 3: Validation

Use this when validating phase completion against targets.

```
I'm validating [Phase] against the plan.

Targets from plan:
[Copy targets from plan]

Current measurements:
[Your measurements with actual numbers]

Context:
- Phase: [Phase Name]
- Days: [Day range]
- Feature flags: [All flags and their status]

Please:
1. Compare current vs target for each metric
2. Identify which targets passed/failed
3. For failures: suggest fixes or recommend disabling feature
4. Calculate overall impact vs baseline
5. Recommend: Proceed to next phase or iterate?
6. Update PROGRESS.md and metrics_tracking.md accordingly
```

### Example Usage

```
I'm validating P0 Quick Wins (Phase 4) against the plan.

Targets from plan:
1. Context Validation: Tier 0 < 10ms ✓
2. Confidence Expression: User trust +20% ✓
3. Temperature Adjustment: Hallucination -10% ✓
4. Grounding Checks: Catch 30%+ ✓
5. Combined: Hallucination < 8%

Current measurements:
1. Tier 0 latency: 8ms (was 350ms) - PASS
2. User trust: +23% (5 user survey) - PASS
3. Hallucination rate: -12% reduction - PASS
4. Grounding catch rate: 34% - PASS
5. Combined hallucination: 7.2% (was 18.5% baseline) - PASS

Context:
- Phase: P0 Validation (Days 20-22)
- Days: Day 22
- Feature flags: All P0 flags = true

Please:
1. Verify all 5 targets are met
2. Confirm all targets passed
3. No fixes needed - all green
4. Overall impact: -61% hallucination rate
5. Recommendation: PROCEED TO P1
6. Update docs/implementation/PROGRESS.md and metrics_tracking.md
```

---

## Template 4: Architecture Audit

Use this for Phase 1 audit tasks.

```
I'm auditing [System/Component] as part of Day [X] architecture audit.

Component: [Name]
File: [Path]
Purpose: [What it does]

Audit goals:
1. [Goal 1 from plan]
2. [Goal 2 from plan]
3. [Goal 3 from plan]

Please:
1. Read the file and related dependencies
2. Document current implementation
3. Measure relevant metrics (latency, accuracy, etc.)
4. Identify integration points with other systems
5. Suggest optimization opportunities
6. Create audit report section for [Component]

Output format:
- Current state summary
- Measurements with numbers
- Integration points (with line numbers)
- Optimization opportunities
- Recommendations
```

### Example Usage

```
I'm auditing the Intent Classification System as part of Day 1-2 architecture audit.

Component: UnifiedIntentClassifier (HassIL-based)
File: services/zoe-core/intent_system/classifiers/hassil_classifier.py
Purpose: Multi-tier intent classification (HassIL → Keyword → LLM fallback)

Audit goals:
1. Verify current functionality and accuracy
2. Measure latency by tier
3. Document integration with chat router
4. Identify optimization opportunities

Please:
1. Read hassil_classifier.py and understand the three-tier system
2. Document how Tier 0/1/2 classification works
3. Measure: Tier 0 latency, Tier 1 latency, Tier 2 latency
4. Find integration points in routers/chat.py (with line numbers)
5. Suggest: caching opportunities, pattern optimization
6. Create audit report for Intent System

Output format:
- Current state: HassIL patterns, keyword fallback, LLM fallback
- Measurements: Tier 0 = Xms, Tier 1 = Yms, Tier 2 = Zms
- Integration: chat.py line 87 calls classify()
- Optimizations: Consider caching frequent intents
- Recommendations: System is working well, ready for P0 enhancements
```

---

## Template 5: Decision Gate Evaluation

Use this at decision gates (Days 5, 22, 38).

```
I'm evaluating the [Gate Name] decision gate on Day [X].

Gate criteria:
[Copy criteria from plan]

Current status:
[List each criterion with PASS/FAIL]

Context:
- Phase completed: [Phase Name]
- Days spent: [X days]
- Issues encountered: [List or NONE]

Decision options:
- PASS: Proceed to [Next Phase]
- FAIL: [Specific action from plan]

Please:
1. Review all gate criteria
2. Evaluate PASS/FAIL for each
3. If FAIL: Identify root cause and remediation
4. Make recommendation: PASS or FAIL
5. Update decision_log.md with decision and rationale
6. If PASS: Prepare summary for next phase kickoff
```

### Example Usage

```
I'm evaluating the P0 Validation decision gate on Day 22.

Gate criteria:
- All 4 P0 targets met
- Combined hallucination < 8%
- No critical bugs
- User feedback positive

Current status:
- Context Validation target: PASS (Tier 0 = 8ms)
- Confidence Expression target: PASS (trust +23%)
- Temperature Adjustment target: PASS (hallucination -12%)
- Grounding Checks target: PASS (catch 34%)
- Combined hallucination: PASS (7.2%)
- Critical bugs: NONE
- User feedback: POSITIVE (4.2/5.0 average)

Context:
- Phase completed: P0 Quick Wins (Days 8-19) + Validation (Days 20-22)
- Days spent: 15 days (on schedule)
- Issues encountered: NONE

Decision options:
- PASS: Proceed to P1 Foundation
- FAIL: Disable failed features, iterate 3-5 days

Please:
1. All 4 targets met: YES
2. All criteria: PASS
3. No failures to remediate
4. Recommendation: PASS - proceed to P1
5. Update decision_log.md with Day 22 gate PASSED
6. Prepare P1 kickoff summary
```

---

## Template 6: Integration Testing

Use this when testing feature integration.

```
I'm testing [Feature] integration with [System/Component].

Feature: [Name]
Feature flag: [Flag name] = [status]
Integration point: [Where it connects]
File: [Path]

Test scenarios:
1. [Scenario 1]
2. [Scenario 2]
3. [Scenario 3]

Expected behavior:
- [Behavior 1]
- [Behavior 2]

Please:
1. Enable feature flag
2. Run test scenarios
3. Verify expected behavior
4. Check logs for errors
5. Measure performance impact
6. Document results
7. Provide commands to reproduce tests
```

### Example Usage

```
I'm testing Context Validation integration with the chat router.

Feature: Context Validation (P0-1)
Feature flag: USE_CONTEXT_VALIDATION = true
Integration point: Context fetching in chat.py
File: services/zoe-core/routers/chat.py

Test scenarios:
1. Tier 0 intent (turn on lights) - should SKIP context
2. Tier 0 data intent (show calendar) - should FETCH context
3. Complex query (>15 words) - should FETCH context
4. Memory keyword query (remember Arduino) - should FETCH context

Expected behavior:
- Tier 0 latency < 10ms for skipped context
- Correct context fetched for data/complex queries
- Logs show "Context SKIPPED" or "Context REQUIRED"
- No increase in hallucination rate

Please:
1. Set USE_CONTEXT_VALIDATION=true in docker-compose.yml
2. Run test scenarios via chat interface
3. Verify: Tier 0 fast, data queries still work
4. Check: docker logs zoe-core --tail 50 for context logs
5. Measure: Average Tier 0 latency (target < 10ms)
6. Document: All scenarios pass/fail
7. Commands: pytest tests/integration/test_context_validation.py
```

---

## Quick Reference

### Common Commands

```bash
# Enable feature flag
export USE_CONTEXT_VALIDATION=true

# Rebuild service
docker compose up -d --build zoe-core

# View logs
docker logs zoe-core --tail 50 -f

# Run specific test
pytest tests/integration/test_p0_validation.py -v

# Run all integration tests
pytest tests/integration/ -v

# Check system health
./scripts/maintenance/check_system_health.sh

# Measure baseline
pytest tests/integration/test_hallucination_benchmark.py --report=baseline.json
```

### File Locations Reference

```
Plan: /memory-enhancement.plan.md
Progress: /home/zoe/assistant/docs/implementation/PROGRESS.md
Metrics: /home/zoe/assistant/docs/implementation/metrics_tracking.md
Decisions: /home/zoe/assistant/docs/implementation/decision_log.md

Core: /home/zoe/assistant/services/zoe-core/
Tests: /home/zoe/assistant/tests/integration/
Config: /home/zoe/assistant/services/zoe-core/config.py
```

---

## Tips for Cursor Usage

1. **Start each session** with Template 1 to set context
2. **Use Template 3** at end of each phase for validation
3. **Use Template 5** at decision gates (Days 5, 22, 38)
4. **Keep prompts specific** - reference exact file paths and line numbers
5. **Include measurements** - always provide actual numbers, not just PASS/FAIL
6. **Update tracking docs** - PROGRESS.md and metrics_tracking.md after each phase
7. **One feature at a time** - don't mix P0-1 and P0-2 in same prompt
8. **Test before moving on** - validate each feature works before next

