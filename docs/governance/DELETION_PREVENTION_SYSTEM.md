# Deletion Prevention System
**Created:** 2025-10-19
**Purpose:** Prevent accidental deletion of critical files

## The Problem
During "ultra-aggressive cleanup" commit 0beb480:
- 64 working router files deleted (~20,000 lines of code)
- Core utility files removed (route_llm.py, etc.)
- UI components deleted
- No dependency analysis done beforehand
- No testing before commit

## 7-Layer Prevention System

### Layer 1: Critical Files Manifest
**File:** `.zoe/critical-files.json`
**Purpose:** Explicit list of files that must never be deleted

```json
{
  "critical_routers": [
    "services/zoe-core/routers/chat.py",
    "services/zoe-core/routers/lists.py",
    "services/zoe-core/routers/calendar.py",
    "services/zoe-core/routers/memories.py",
    "services/zoe-core/routers/journal.py",
    "services/zoe-core/routers/auth.py"
  ],
  "critical_utilities": [
    "services/zoe-core/main.py",
    "services/zoe-core/router_loader.py",
    "services/zoe-core/ai_client.py"
  ],
  "critical_frontend": [
    "services/zoe-ui/dist/js/common.js",
    "services/zoe-ui/dist/js/auth.js",
    "services/zoe-ui/dist/css/glass.css"
  ]
}
```

### Layer 2: Pre-Deletion Validation Script
**File:** `tools/audit/validate_before_delete.py`

```python
#!/usr/bin/env python3
"""
Validate files before deletion
Usage: python3 validate_before_delete.py file1.py file2.py ...
"""
import sys
import json
from pathlib import Path

def check_file_safety(filepath):
    # Load critical files manifest
    # Check if file is in critical list
    # Find references to file in codebase
    # Report dependencies
    pass
```

### Layer 3: Enhanced Pre-Commit Hook
**File:** `.git/hooks/pre-commit`

Add deletion validation:
```bash
# Check if any critical files are being deleted
deleted_files=$(git diff --cached --name-status | grep '^D' | awk '{print $2}')
if [ -n "$deleted_files" ]; then
    python3 tools/audit/validate_before_delete.py $deleted_files || exit 1
fi
```

### Layer 4: File Dependency Map
**File:** `.zoe/file-dependencies.json`
Auto-generated map showing what imports what:

```json
{
  "services/zoe-core/routers/chat.py": {
    "imports": ["route_llm.py", "ai_client.py"],
    "imported_by": ["main.py"],
    "database_tables": ["chat_sessions", "messages"],
    "endpoints": ["/api/chat/*"]
  }
}
```

### Layer 5: Automated Testing Before Cleanup
**Script:** `tools/cleanup/safe_delete.py`

```python
# Before deleting ANY file:
# 1. Run all tests
# 2. Check dependencies
# 3. Create backup branch
# 4. Test with file removed
# 5. Only proceed if all tests pass
```

### Layer 6: Router Registry
**File:** `services/zoe-core/ROUTER_REGISTRY.md`

Documented list of ALL routers with:
- Purpose
- Endpoints
- Dependencies
- Database tables used
- Critical: Yes/No
- Redundant with: (if duplicate)

### Layer 7: Code Review Checklist
**Before ANY cleanup commit:**

- [ ] Created backup branch
- [ ] Ran `validate_before_delete.py` on all files
- [ ] Checked file dependencies
- [ ] Ran full test suite
- [ ] Tested UI manually
- [ ] Tested all API endpoints
- [ ] Reviewed with AI assistant for unintended consequences
- [ ] Created detailed commit message explaining deletions
- [ ] Can rollback easily if issues found

## Implementation Plan

1. **Immediate** (Today):
   - [ ] Create critical-files.json
   - [ ] Create validate_before_delete.py
   - [ ] Update pre-commit hook
   - [ ] Document current router purposes

2. **Short-term** (This Week):
   - [ ] Build file dependency mapper
   - [ ] Create router registry
   - [ ] Write safe_delete.py tool
   - [ ] Add to .cursorrules

3. **Long-term** (Ongoing):
   - [ ] Keep manifest updated
   - [ ] Review dependencies quarterly
   - [ ] Archive truly unused code (don't delete)
   - [ ] Document before removing

## File Naming Convention for "Maybe Delete"

Instead of deleting files you're unsure about:

```
routers/
  ├── lists.py                    # Active
  ├── _deprecated/
  │   ├── lists_redesigned.py    # Deprecated but kept
  │   └── README.md              # Why deprecated
  └── _candidates_for_removal/
      ├── old_chat.py            # Candidate for removal
      └── REMOVAL_PLAN.md        # Analysis before removal
```

## Standardize Database Path

**Current Issue:** Two paths to same database
```
/app/data/zoe.db           # Via ./data:/app/data
/home/zoe/assistant/data/zoe.db   # Via /home/zoe/assistant:/home/zoe/assistant
```

**Solution:**
1. Use ONLY `/app/data/zoe.db` for database access
2. Keep `/home/zoe/assistant` mount for code/config only
3. Ensure `DATABASE_PATH=/app/data/zoe.db` in all routers
4. Update all routers to use env var, not hardcoded paths

## Emergency Recovery Plan

If files are accidentally deleted:

1. **DO NOT PANIC**
2. **DO NOT make reactive changes**
3. **Check git history**: `git log --all --oneline -- path/to/file`
4. **Create recovery branch**: `git checkout -b recovery-YYYYMMDD`
5. **Restore selectively**: `git show COMMIT:path/to/file > path/to/file`
6. **Test incrementally**: Test each restored file
7. **Document**: Why it was restored

## Rules for AI Assistants (for .cursorrules)

```markdown
### BEFORE DELETING ANY FILE:
1. MUST run validate_before_delete.py
2. MUST check critical-files.json
3. MUST search codebase for imports/references
4. MUST test with file removed
5. MUST create backup branch
6. MUST get user approval for router deletions

### NEVER DELETE:
- Any file in critical-files.json
- Any file imported by 3+ other files
- Any router without dependency analysis
- Database migration files
- Configuration files without backup
```

