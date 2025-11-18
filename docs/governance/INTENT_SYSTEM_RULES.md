# Intent System Development Rules
# MANDATORY - ALL DEVELOPERS MUST FOLLOW

Last Updated: 2025-11-17

## 🛡️ CORE PRINCIPLES

### 1. NEVER BYPASS THE INTENT LAYER
- ❌ WRONG: Add new chat.py logic that bypasses intent classification
- ✅ RIGHT: Add new intent pattern, handler calls existing router

### 2. NEVER MODIFY ROUTER SIGNATURES FOR INTENTS
- ❌ WRONG: Change lists.py functions to accept "intent" parameter
- ✅ RIGHT: Handlers adapt intents to existing router parameters

### 3. NEVER DELETE INTENT PATTERNS WITHOUT MIGRATION
- ❌ WRONG: Remove old patterns immediately
- ✅ RIGHT: Deprecate → migrate users → remove after 3 months

### 4. ALWAYS MAINTAIN DUAL INTERFACE
- ❌ WRONG: Remove REST API endpoints when intent is added
- ✅ RIGHT: Keep both REST and intent paths forever

## 🚫 PROHIBITED ACTIONS

### NEVER:
1. Add command detection (if/else, regex) to chat.py or voice agent
2. Create chat_v2.py, chat_new.py, or duplicate routers
3. Hardcode responses instead of using handlers
4. Skip intent tests when adding new patterns
5. Deploy without running intent validation
6. Modify routers to "intent-aware" (they should be ignorant)
7. Add LLM calls to Tier 0 handlers (defeats purpose)
8. Break backward compatibility on REST APIs

## ✅ REQUIRED PROCESS FOR CHANGES

### Adding New Intent:
1. Create YAML pattern in `intent_system/intents/en/[domain].yaml`
2. Add handler in `intent_system/handlers/[domain]_handlers.py`
3. Register in `intent_executor.py` HANDLERS dict
4. Add classification test (`tests/intent_system/test_classification.py`)
5. Add execution test (`tests/intent_system/test_execution.py`)
6. Update `docs/user/VOICE_COMMANDS.md` with examples
7. Run validation: `python tools/intent/validate_intents.py`
8. Run tests: `pytest tests/intent_system/`
9. Commit with message: `"feat(intent): add [IntentName]"`

### Modifying Existing Intent:
1. Create feature branch
2. Update YAML pattern (maintain backward compatibility)
3. Update handler if needed
4. Update tests
5. Run regression tests (all tests must pass)
6. Deploy to staging with feature flag
7. Monitor analytics for 48 hours
8. Merge if success rate >95%

### Removing Intent:
1. NEVER remove immediately
2. Mark as deprecated in YAML (add comment)
3. Add migration notice to logs
4. Wait 90 days minimum
5. Verify zero usage in analytics
6. Then remove with approval

## 🔍 MANDATORY VALIDATIONS

### Pre-Commit Hooks (Automatic):
- Validate YAML syntax (all intent files)
- Verify handler registration (no orphaned intents)
- Check test coverage (every intent has tests)
- Lint handler code (type hints, docstrings)
- Check for hardcoded responses
- Verify no LLM calls in Tier 0 handlers

### CI/CD Checks (Automatic):
- Run all intent tests
- Performance benchmarks (latency targets)
- Tier distribution validation (85%+ Tier 0/1)
- Integration tests (all interfaces)
- API compatibility tests (REST still works)

### Manual Review Required For:
- New domain (new YAML file)
- Changes to `intent_executor.py`
- Changes to `hassil_classifier.py`
- Performance-critical handlers
- Security-sensitive intents

## 📊 QUALITY GATES

### Every Intent Must Have:
- ✅ At least 5 pattern variations
- ✅ Unit test with 3+ test cases
- ✅ Execution test (end-to-end)
- ✅ Example in `VOICE_COMMANDS.md`
- ✅ Handler with error handling
- ✅ Natural language response template
- ✅ Performance target met (<5ms for Tier 0)

### Every Handler Must Have:
- ✅ Type hints on all parameters
- ✅ Docstring with intent name and slots
- ✅ Input validation
- ✅ Error handling (try/catch)
- ✅ Logging for failures
- ✅ Return structured result dict
- ✅ WebSocket broadcast if data changes

### Every Pattern Must Have:
- ✅ Default slot values where applicable
- ✅ Natural variations (not just one rigid pattern)
- ✅ Consideration for pronouns ("it", "that", "those")
- ✅ Common misspellings/variations

## 🏗️ ARCHITECTURE RULES

### Intent System Structure (MUST NOT CHANGE):
```
services/zoe-core/intent_system/
├── classifiers/          # Intent classification logic
│   ├── hassil_classifier.py      # CORE - Tier 0/1
│   └── context_manager.py        # CORE - Tier 2
├── executors/            # Intent execution
│   ├── intent_executor.py        # CORE - Central router
│   └── handler_adapters.py       # Adapters (modify OK)
├── handlers/             # Domain handlers (add new OK)
│   ├── lists_handlers.py
│   ├── calendar_handlers.py
│   └── ...
├── intents/              # YAML patterns (add/modify OK)
│   └── en/
│       ├── lists.yaml
│       └── ...
├── formatters/           # Response templates (modify OK)
│   └── response_formatter.py
└── analytics/            # Metrics (modify OK)
    └── metrics.py
```

**CORE files**: Require architecture review before modification
**Extension files**: Can be modified following process above

### Router Integration Rules:
- Intent system calls routers, NOT vice versa
- Routers remain intent-agnostic
- Use handler adapters to bridge intent ↔ router
- Never import intent system in routers

### Feature Flag Rules:
- All new intent domains behind feature flag initially
- Format: `USE_INTENT_[DOMAIN]=true/false`
- Must be rollbackable in <1 minute
- Document flag in `.env.example`

## 🚨 BREAKING CHANGE POLICY

### What Constitutes Breaking Change:
- Removing intent pattern (users' commands stop working)
- Changing slot names (programmatic users break)
- Removing REST endpoint (integrations break)
- Changing response format (parsers break)

### Breaking Changes Require:
1. Architecture review meeting
2. Migration guide document
3. Deprecation notice (90 days minimum)
4. Version bump (major version)
5. Communication to all users
6. Rollback plan tested

### Non-Breaking Changes:
- Adding new intent patterns (always safe)
- Adding new handlers (always safe)
- Improving response templates (safe if tested)
- Performance optimizations (safe if validated)

## 📈 PERFORMANCE REQUIREMENTS

### Every Handler Must Meet:
- Tier 0: <5ms execution (or demote to Tier 2)
- Tier 1: <15ms execution (or demote to Tier 2)
- Success rate: >95% (or fix patterns)
- Error rate: <0.1% (or add error handling)

### If Performance Degrades:
1. Analytics dashboard will alert
2. Review slow handlers (identify bottleneck)
3. Optimize or demote to lower tier
4. Never sacrifice correctness for speed

## 🔐 SECURITY RULES

### Intent Handlers Must:
- Validate user permissions BEFORE execution
- Sanitize all slot values (prevent injection)
- Never trust user_id from client (use session)
- Log security-relevant actions
- Rate limit per user (prevent abuse)

### Forbidden in Handlers:
- Direct SQL without parameterization
- `os.system()` or subprocess with user input
- `eval()` or `exec()` on any input
- Exposing internal paths or secrets
- Bypassing authentication

## 📝 COMMIT MESSAGE FORMAT

### Format:
```
feat(intent): add ListShare intent for sharing lists
fix(intent): improve calendar date parsing
perf(intent): optimize HassIL pattern matching
docs(intent): update voice commands guide
test(intent): add ListAdd edge cases
```

### Scope: Always use "intent" for intent system changes

## 🆘 IF YOU BREAK SOMETHING

### Immediate Actions:
1. STOP - Don't commit more changes
2. Check analytics dashboard for impact
3. If >5% error rate: Rollback immediately
4. Document what broke and why
5. Create incident report
6. Fix root cause (not just symptoms)
7. Add test to prevent recurrence
8. Update rules if needed

### Rollback Procedure:
```bash
# Rollback specific interface
export USE_INTENT_CHAT=false
docker restart zoe-core

# Emergency full rollback
git revert HEAD
docker compose restart

# Nuclear option (restore from backup)
bash scripts/deployment/restore_snapshot.sh <snapshot_id>
```

## 📚 REFERENCE DOCUMENTATION

Must read before modifying intent system:
- `docs/architecture/WHY_HASSIL.md`
- `docs/developer/INTENT_SYSTEM.md`
- `docs/api/INTENT_API.md`
- This file (`INTENT_SYSTEM_RULES.md`)

## ✅ CHECKLIST BEFORE COMMIT

Copy this checklist for every intent system change:

```
[ ] YAML syntax valid (ran validator)
[ ] Handler registered in intent_executor.py
[ ] Unit test added/updated
[ ] Integration test passes
[ ] Performance target met
[ ] Documentation updated
[ ] No hardcoded responses
[ ] No LLM calls in Tier 0
[ ] Error handling added
[ ] Logging added
[ ] WebSocket broadcast if needed
[ ] Security validated
[ ] Backward compatibility maintained
[ ] Feature flag configured (if new domain)
[ ] All tests passing (pytest)
[ ] Pre-commit hooks passed
```

---

**REMEMBER**: This system handles 90%+ of user interactions. Breaking it affects EVERYONE. Follow the rules or face failed deployments. 🔥

