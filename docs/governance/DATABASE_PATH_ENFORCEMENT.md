# Database Path Enforcement System
**Created**: October 23, 2025  
**Status**: ✅ ACTIVE & ENFORCED

## Problem That Occurred

### What Happened (Oct 23, 2025)
- System was working perfectly this morning
- New reminder services were added at 7:15 PM
- Services had hardcoded database paths: `/home/zoe/assistant/data/zoe.db`
- This broke the system immediately with `unable to open database file` errors

### Root Cause
Docker container path mapping:
- **Host filesystem**: `/home/zoe/assistant/data/zoe.db`
- **Container filesystem**: `/app/data/zoe.db`
- **Environment variable**: `DATABASE_PATH=/app/data/zoe.db`

New services used hardcoded host paths, which don't exist inside containers.

## The Solution

### 1. **Automated Enforcement Tool** ✅
**File**: `tools/audit/check_database_paths.py`

Scans all Python files for hardcoded database paths and blocks commits.

**Run manually**:
```bash
python3 tools/audit/check_database_paths.py
```

### 2. **Pre-Commit Hook Integration** ✅
**File**: `.git/hooks/pre-commit` (updated)

Automatically runs before every commit to prevent hardcoded paths from entering the codebase.

### 3. **Documentation Updated** ✅
**File**: `PROJECT_STRUCTURE_RULES.md`

Added mandatory database path rules with correct/incorrect patterns.

## Standard Pattern - MUST USE

### ✅ CORRECT:
```python
import os

class SomeService:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.getenv("DATABASE_PATH", "/home/zoe/assistant/data/zoe.db")
        self.db_path = db_path
```

### ❌ WRONG (Will Break):
```python
class SomeService:
    def __init__(self, db_path: str = "/home/zoe/assistant/data/zoe.db"):  # HARDCODED!
        self.db_path = db_path
```

## What Was Fixed Today

### Files Corrected:
1. `services/zoe-core/services/calendar_reminder_service.py`
2. `services/zoe-core/services/task_reminder_service.py`
3. `services/zoe-core/services/push_notification_service.py`

All now properly use `os.getenv("DATABASE_PATH")`.

## How This Prevents Future Issues

### Protection Layers:
1. **Pre-commit hook** - Blocks bad code before it's committed
2. **Documentation** - Clear examples in PROJECT_STRUCTURE_RULES.md
3. **Audit tool** - Can be run anytime to check compliance
4. **Related docs** - DATABASE_CONSOLIDATION_PLAN.md has additional context

### What Happens Now:
```bash
# Try to commit code with hardcoded path
git commit -m "Add new service"

# Pre-commit hook runs:
🔍 Running pre-commit validations...
Checking for hardcoded database paths...
❌ DATABASE PATH VIOLATIONS DETECTED
Use os.getenv('DATABASE_PATH') instead of hardcoded paths

# Commit BLOCKED - must fix first
```

## Existing Violations (Non-Critical)

The following files still have hardcoded paths but are not critical for production:

### Scripts (Utilities - Not in Docker):
- `scripts/utilities/migrate_to_light_rag.py`
- `scripts/utilities/light_rag_benchmarks.py`
- `scripts/utilities/add_enhanced_tasks.py`

### Tests (Run outside Docker):
- `tests/unit/test_mcp_server.py`
- `tests/unit/test_mcp_security.py`

### Tools (Run on host):
- `tools/audit/comprehensive_audit.py`

These can be fixed gradually as they're not running inside Docker containers where the path mapping is an issue.

## Testing the Enforcement

```bash
# Check current status
python3 tools/audit/check_database_paths.py

# Should show any violations
# Services should pass ✅
```

## Why This Matters

### Before Enforcement:
- ❌ New code could break production
- ❌ Container restarts would fail
- ❌ Services couldn't access databases
- ❌ Silent failures with cryptic errors

### After Enforcement:
- ✅ All new code validated automatically
- ✅ Container path issues caught immediately
- ✅ Clear error messages with fix instructions
- ✅ Cannot commit broken code

## Related Documentation

- **PROJECT_STRUCTURE_RULES.md** - Full project rules including database paths
- **DATABASE_CONSOLIDATION_PLAN.md** - Overall database architecture plan
- **DELETION_PREVENTION_SYSTEM.md** - Other protection mechanisms
- **.cursorrules** - AI assistant rules (should reference this)

## Rollout

- ✅ **Oct 23, 2025 7:45 PM** - System created and deployed
- ✅ **Oct 23, 2025 7:50 PM** - Production services fixed
- ✅ **Oct 23, 2025 8:00 PM** - Pre-commit hook active
- ✅ **Oct 23, 2025 8:05 PM** - Documentation complete

**Status**: Fully operational and enforced.





