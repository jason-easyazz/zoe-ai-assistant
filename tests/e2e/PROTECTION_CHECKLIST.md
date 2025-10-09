# E2E Test Fixes - Protection Checklist

## ✅ What's Already Protected

### 1. Host Files (PERSISTENT)
All changes made to these files are **permanent**:
- ✅ `/home/pi/zoe/services/mem-agent/enhanced_mem_agent_service.py`
- ✅ `/home/pi/zoe/services/mem-agent/reminder_expert.py`
- ✅ `/home/pi/zoe/services/mem-agent/homeassistant_expert.py`
- ✅ `/home/pi/zoe/services/zoe-core/routers/chat.py`
- ✅ `/home/pi/zoe/services/zoe-core/routers/reminders.py`
- ✅ `/home/pi/zoe/services/zoe-core/temporal_memory.py`
- ✅ `/home/pi/zoe/services/zoe-core/temporal_memory_integration.py`
- ✅ `/home/pi/zoe/services/zoe-core/enhanced_mem_agent_client.py`

### 2. Database Changes (PERSISTENT)
- ✅ `/home/pi/zoe/data/zoe.db` - Reminders columns added
- ✅ `/home/pi/zoe/data/memory.db` - Temporal memory schema

### 3. Test Files (PERSISTENT)
- ✅ All test files in `/home/pi/zoe/tests/e2e/`
- ✅ Documentation files created

## ⚠️ What Needs Verification

### Docker Volume Mounts
**Check if containers auto-reload host files:**

```bash
# If services/mem-agent is mounted to /app:
docker inspect mem-agent | grep -A10 Mounts

# If NOT mounted, need to rebuild:
docker-compose build mem-agent
docker-compose build zoe-core
```

## 🔒 Protection Mechanisms to Add

### 1. Git Commit (CRITICAL)
```bash
cd /home/pi/zoe
git add services/mem-agent/
git add services/zoe-core/
git add tests/e2e/
git add *.md
git commit -m "feat: E2E tests 100% - All 43 tests passing

- Fixed ReminderExpert time parsing and API schema
- Fixed ListExpert shopping query detection  
- Fixed JournalExpert endpoint path
- Fixed HomeAssistantExpert service calls
- Fixed temporal memory with conversation_turns table
- Added safety guidance to prevent false refusals
- All 8 experts loading and executing correctly

Fixes: #[issue_number]
Tests: 43/43 passing (100%)"
```

### 2. Pre-Commit Hook
```bash
cat > /home/pi/zoe/.git/hooks/pre-commit << 'HOOK'
#!/bin/bash
echo "🧪 Running E2E test suite..."
cd /home/pi/zoe
timeout 600 python3 tests/e2e/run_all_tests_detailed.py > /tmp/pre-commit-test.log 2>&1
if [ $? -ne 0 ]; then
    echo "❌ E2E tests failed! See /tmp/pre-commit-test.log"
    tail -50 /tmp/pre-commit-test.log
    exit 1
fi
echo "✅ All 43 E2E tests passing"
HOOK

chmod +x /home/pi/zoe/.git/hooks/pre-commit
```

### 3. CI/CD Pipeline (Recommended)
**Add to `.github/workflows/e2e-tests.yml`:**
```yaml
name: E2E Tests
on: [push, pull_request]
jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Start Services
        run: docker-compose up -d
      - name: Run E2E Tests
        run: python3 tests/e2e/run_all_tests_detailed.py
      - name: Upload Report
        uses: actions/upload-artifact@v3
        with:
          name: e2e-test-report
          path: tests/e2e/detailed_test_report.json
```

### 4. Database Backup
```bash
# Backup script
cat > /home/pi/zoe/scripts/maintenance/backup_databases.sh << 'SCRIPT'
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p /home/pi/zoe/backups/databases
cp /home/pi/zoe/data/zoe.db /home/pi/zoe/backups/databases/zoe_$DATE.db
cp /home/pi/zoe/data/memory.db /home/pi/zoe/backups/databases/memory_$DATE.db
echo "✅ Databases backed up to backups/databases/"
SCRIPT

chmod +x /home/pi/zoe/scripts/maintenance/backup_databases.sh
```

### 5. Documentation Index
**File:** `tests/e2e/README.md`
```markdown
# E2E Test Suite

## Current Status: 100% (43/43 passing)

## Test Suites
- `run_all_tests_detailed.py` - Main 43-test comprehensive suite
- `test_chat_comprehensive.py` - Original 10-test suite
- `test_natural_language_comprehensive.py` - Original 33-test suite

## Reports
- `ALL_43_TESTS_QA.txt` - Complete Q&A results
- `detailed_test_report.json` - JSON test data
- `COMPREHENSIVE_FINAL_SUMMARY.md` - Full analysis

## Critical Files - DO NOT BREAK
- `services/mem-agent/enhanced_mem_agent_service.py` - Expert loading
- `services/mem-agent/reminder_expert.py` - Time parsing, API payload
- `services/zoe-core/routers/reminders.py` - reminder_time calculation
- `services/zoe-core/temporal_memory_integration.py` - Conversation history

## How to Run
```bash
cd /home/pi/zoe
python3 tests/e2e/run_all_tests_detailed.py
```

Expected: 43/43 passing (100%)
```

## Immediate Action Required

### Run This Now to Persist Everything:
```bash
cd /home/pi/zoe

# 1. Rebuild containers with updated code
docker-compose build mem-agent

# 2. Verify test still passes after rebuild
docker-compose up -d
sleep 15
python3 tests/e2e/run_all_tests_detailed.py

# 3. Commit if tests pass
git add .
git commit -m "feat: E2E tests 100% passing with all expert fixes"
```

## Summary

✅ **Changes Documented**: 7 files with detailed documentation  
✅ **Host Files Updated**: All fixes in `/home/pi/zoe/services/`  
⚠️ **Container Changes**: Need docker-compose rebuild to persist  
✅ **Database Changes**: Already persistent (volume mounted)  
✅ **PersonExpert**: **NOT RECOMMENDED** - use existing people-service instead  

**Next Step:** Run `docker-compose build` to bake changes into images.

