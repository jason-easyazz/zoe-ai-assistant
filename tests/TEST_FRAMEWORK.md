# 🧪 Zoe Test Framework

**Purpose**: Comprehensive testing system that validates the entire stack  
**Critical Use**: Run after ANY AI/LLM changes to ensure UI chat still works

---

## 🎯 Test Strategy

### Why This Exists
**Problem**: Making changes to AI components breaks UI chat  
**Solution**: Comprehensive test suite that validates everything works together  
**Result**: Catch breaking changes before they reach production

---

## 📁 Test Structure

```
tests/
├── integration/
│   ├── test_full_system.py         ⭐ Main integration test
│   ├── test_api_endpoints.py       API endpoint tests
│   ├── test_ui_chat_integration.py UI + API integration
│   └── full_system_test_report.json Latest test results
│
├── unit/
│   ├── test_reminders_api.py       Unit tests for reminders
│   ├── test_calendar_api.py        Unit tests for calendar
│   └── ...
│
├── e2e/
│   └── test_user_workflows.py      Complete user journeys
│
└── fixtures/
    ├── test_data.json              Test data
    └── mock_responses.json         Mock AI responses
```

---

## 🚀 Quick Start

### Run Full System Test
```bash
# Quick test (essential checks)
python3 tests/integration/test_full_system.py

# With pytest (more detailed)
pytest tests/integration/test_full_system.py -v

# All integration tests
pytest tests/integration/ -v
```

### After Making Changes
```bash
# 1. Make your changes to AI/LLM code
vim services/zoe-core/routers/chat.py

# 2. Run full system test
python3 tests/integration/test_full_system.py

# 3. If chat test passes ✅ → UI will work
# 4. If chat test fails ❌ → Fix before deploying
```

---

## 🧪 What Gets Tested

### Critical Path (Must Pass for UI to Work)
1. ✅ Health endpoint responds
2. ✅ Database accessible
3. ✅ Lists API working
4. ✅ Calendar API working
5. ✅ Reminders API working (recently fixed!)
6. ✅ **Chat API working** ⭐ MOST IMPORTANT
7. ✅ AI components accessible
8. ✅ UI files exist

### Current Test Coverage
- **API Endpoints**: 11/11 endpoints tested
- **Services**: Docker, database, AI components
- **UI Dependencies**: Critical files checked
- **Integration**: Chat → AI → Database → UI

---

## 📊 Adding New Tests

### When to Add a Test

**Add test when**:
- New API endpoint created
- New feature added that UI uses
- New service integrated
- New tool that affects chat

**Example**: You add a new `/api/weather` endpoint

### How to Add a Test

#### Step 1: Create Test Function
```python
# tests/integration/test_full_system.py

def test_weather_api(self):
    """Test weather API endpoints"""
    print(f"\n{Colors.BLUE}Testing: Weather API...{Colors.RESET}")
    
    try:
        response = requests.get(
            f"{API_BASE}/api/weather",
            params={"user_id": TEST_USER_ID, "location": "Perth"},
            timeout=5
        )
        assert response.status_code == 200, f"Weather failed: {response.status_code}"
        
        self.results["passed"].append("Weather API")
        print(f"{Colors.GREEN}✓ Weather API{Colors.RESET}")
        return True
    except Exception as e:
        self.results["failed"].append(("Weather API", str(e)))
        print(f"{Colors.RED}✗ Weather API: {e}{Colors.RESET}")
        return False
```

#### Step 2: Add to Test Suite
```python
# In run_all_tests() method
tests = [
    # ... existing tests ...
    ("Weather API", self.test_weather_api),  # Add here
]
```

#### Step 3: Test It
```bash
python3 tests/integration/test_full_system.py
```

That's it! The test is now part of the suite.

---

## 🎯 Test Categories

### 1. Critical Tests (Must Pass)
These MUST pass or UI won't work:
- Health Check
- Database
- Chat API ⭐
- UI Files

**If these fail**: Don't deploy. Fix immediately.

### 2. Important Tests (Should Pass)
These should pass for full functionality:
- Lists API
- Calendar API
- Reminders API

**If these fail**: Features won't work, but UI chat might still work.

### 3. Optional Tests (Nice to Have)
These are for extra features:
- AI Components (Ollama, LiteLLM)
- Docker Services

**If these fail**: Warnings shown, but not blocking.

---

## 🔧 Customizing Tests

### Test Configuration
```python
# At top of test_full_system.py

API_BASE = "http://localhost:8000"
TEST_USER_ID = "test_user_full_system"
TIMEOUT = 30  # Seconds

# Adjust as needed for your environment
```

### Adding Mock Data
```python
# tests/fixtures/test_data.json
{
  "test_messages": [
    "Hello, how are you?",
    "What's on my calendar today?",
    "Add milk to shopping list"
  ],
  "test_user": {
    "user_id": "test_user_full_system",
    "preferences": {}
  }
}
```

### Using Fixtures
```python
import json

def load_test_data():
    with open("tests/fixtures/test_data.json") as f:
        return json.load(f)

def test_chat_api(self):
    test_data = load_test_data()
    for message in test_data["test_messages"]:
        # Test with each message
        pass
```

---

## 📋 Test Checklist Template

### Before Major Changes
```bash
- [ ] Run full system test: `python3 tests/integration/test_full_system.py`
- [ ] Verify all critical tests pass
- [ ] Check no new warnings
- [ ] Review test report
```

### After AI/LLM Changes
```bash
- [ ] Test chat API specifically
- [ ] Test streaming if applicable
- [ ] Verify AI models accessible
- [ ] Test with multiple messages
- [ ] Check response quality
```

### Before Deployment
```bash
- [ ] Full system test passes ✅
- [ ] No critical failures
- [ ] Chat API confirmed working
- [ ] UI files all present
- [ ] Database schema correct
```

---

## 🎨 Extending the Framework

### Adding New Test Module

```python
# tests/integration/test_new_feature.py
"""
Test new feature integration

This module tests the new feature end-to-end
"""

import requests
import pytest

API_BASE = "http://localhost:8000"

def test_new_feature_api():
    """Test new feature API"""
    response = requests.get(f"{API_BASE}/api/new-feature")
    assert response.status_code == 200

def test_new_feature_with_chat():
    """Test new feature works with chat"""
    response = requests.post(
        f"{API_BASE}/api/chat",
        json={"message": "Use new feature"}
    )
    assert response.status_code == 200

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

### Integrating with Main Suite

```python
# In test_full_system.py

from test_new_feature import test_new_feature_api

# Add to run_all_tests():
tests = [
    # ... existing ...
    ("New Feature", self.test_new_feature_api),
]
```

---

## 📊 Test Report Format

### JSON Output
```json
{
  "timestamp": "2025-10-08 12:00:00",
  "results": {
    "passed": ["Health Check", "Chat API", ...],
    "failed": [],
    "warnings": ["Ollama not accessible"]
  },
  "success": true
}
```

### Console Output
```
======================================================================
ZOE FULL SYSTEM INTEGRATION TEST
======================================================================

Testing: Health Check...
✓ Health Check

Testing: Chat API (CRITICAL)...
  ✓ Chat API - Basic Response
  ✓ Chat API - Streaming

======================================================================
TEST RESULTS
======================================================================

Passed: 8/9
Failed: 0
Warnings: 1

✅ PASSED:
  • Health Check
  • Chat API - Basic
  • Chat API - Streaming
  ...

✅ SYSTEM TEST PASSED
✅ UI CHAT CONFIRMED WORKING
```

---

## 🚨 Critical: Chat API Test

### Why It's Most Important

The Chat API test is **CRITICAL** because:
- UI chat depends on it
- Most visible feature to users
- Breaks easily with AI changes
- Hard to debug if broken in production

### What It Tests

```python
def test_chat_api(self):
    # 1. Send message to /api/chat
    # 2. Verify 200 response
    # 3. Verify response has content
    # 4. Verify response is not empty
    # 5. Test streaming endpoint if available
    
    # If ANY fail → UI chat is broken
```

### When Chat Test Fails

**Symptoms**:
- UI shows loading spinner forever
- Error in browser console
- No AI response

**Actions**:
1. Check Ollama running: `curl http://localhost:11434/api/tags`
2. Check zoe-core logs: `docker logs zoe-core-test --tail 50`
3. Test chat directly: `curl -X POST http://localhost:8000/api/chat -d '{"message":"test"}'`
4. Check database: `sqlite3 /home/zoe/assistant/data/zoe.db ".tables"`

---

## 🔄 CI/CD Integration

### GitHub Actions Example

```yaml
# .github/workflows/test.yml
name: Full System Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      zoe-core:
        # ... service definition
    
    steps:
      - uses: actions/checkout@v2
      
      - name: Run Full System Test
        run: python3 tests/integration/test_full_system.py
      
      - name: Upload Test Report
        if: always()
        uses: actions/upload-artifact@v2
        with:
          name: test-report
          path: tests/integration/full_system_test_report.json
```

---

## 📝 Test Development Guidelines

### Writing Good Tests

**✅ DO**:
- Test one thing per function
- Use descriptive names: `test_chat_api_returns_response`
- Add docstrings explaining what's tested
- Handle errors gracefully
- Use appropriate timeouts
- Clean up test data

**❌ DON'T**:
- Test multiple things in one function
- Use vague names: `test1`, `test_stuff`
- Skip error handling
- Use infinite timeouts
- Leave test data in database

### Test Naming Convention

```python
# ✅ GOOD
def test_chat_api_basic_response():
    """Test chat API returns valid response"""
    pass

def test_chat_api_handles_errors():
    """Test chat API error handling"""
    pass

# ❌ BAD
def test_chat():
    pass

def test1():
    pass
```

---

## 🎯 Quick Reference

### Run Tests
```bash
# Full system test (recommended)
python3 tests/integration/test_full_system.py

# With pytest
pytest tests/integration/test_full_system.py -v

# All integration tests
pytest tests/integration/ -v

# Specific test
pytest tests/integration/test_full_system.py::test_chat_api -v
```

### Check Last Results
```bash
cat tests/integration/full_system_test_report.json | jq '.results'
```

### Add New Test
1. Create test function in `test_full_system.py`
2. Add to `tests` list in `run_all_tests()`
3. Run and verify
4. Commit with test passing

---

## ✅ Success Criteria

Test framework is successful when:

- ✅ Tests run in < 1 minute
- ✅ All critical tests pass
- ✅ Easy to add new tests
- ✅ Clear pass/fail output
- ✅ Catches breaking changes
- ✅ Validates UI chat works
- ✅ Generates useful reports

---

**Use this framework after EVERY change to AI/LLM components!**

*See GOVERNANCE.md for integration with development workflow*

