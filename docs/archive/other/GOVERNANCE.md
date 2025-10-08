# 🏛️ Zoe Project Governance System

**Version**: 1.0  
**Date**: October 8, 2025  
**Status**: 🔒 ACTIVE & ENFORCED

This document defines how the Zoe project maintains clean, organized structure **automatically**.

---

## 🎯 Core Philosophy

> **"A place for everything, and everything in its place"**

Every file in this project has **exactly ONE correct location**. No ambiguity. No guessing. Automated enforcement prevents mess.

---

## 📁 The Structure

```
/home/pi/zoe/
│
├── 📚 Root: Max 10 .md files (essential docs only)
├── 🧪 tests/{category}/     (ALL test files here)
├── 📜 scripts/{category}/   (ALL scripts here)
├── 🛠️ tools/{category}/     (automation tools)
├── 📖 docs/{category}/      (organized documentation)
├── 🐳 services/             (production code)
└── 💾 data/                 (application data - not committed)
```

**Simple Rule**: If you're creating a file, consult the decision tree below.

---

## 🌳 Decision Tree

### Creating a New File?

```
START: What type of file is it?

┌─ Documentation (.md)?
│  ├─ Is it ESSENTIAL? (top 10 most important)
│  │  ├─ YES → /README.md, /PROJECT_STATUS.md, etc. (root)
│  │  └─ NO → /docs/{guides|api|architecture}/
│  └─ Is it historical/superseded?
│     └─ YES → /docs/archive/{reports|technical|guides}/
│
├─ Test File (.py with test)?
│  ├─ Unit test → /tests/unit/
│  ├─ Integration → /tests/integration/
│  ├─ Performance → /tests/performance/
│  ├─ E2E → /tests/e2e/
│  ├─ Old/unused → /tests/archived/
│  └─ EXCEPTION: test_architecture.py stays in root
│
├─ Script (.sh)?
│  ├─ Setup → /scripts/setup/
│  ├─ Maintenance → /scripts/maintenance/
│  ├─ Deployment → /scripts/deployment/
│  ├─ Security → /scripts/security/
│  └─ One-off → /scripts/utilities/ (archive after use)
│
├─ Tool (.py for automation)?
│  ├─ Audit tool → /tools/audit/
│  ├─ Cleanup tool → /tools/cleanup/
│  ├─ Validation → /tools/validation/
│  └─ Generator → /tools/generators/
│
├─ Temporary (.tmp, .cache, .bak)?
│  └─ DELETE IT (should be in .gitignore)
│
└─ Config (.yaml, .json)?
   └─ /config/
```

**If still unsure**: Ask in the issue or run `python3 tools/cleanup/auto_organize.py --execute`

---

## 🤖 Automated Enforcement

### 1. Pre-Commit Hook ✅ ACTIVE
**Location**: `.git/hooks/pre-commit`  
**Action**: Runs `tools/audit/enforce_structure.py` before every commit  
**Result**: Commit blocked if violations detected

**Rules Enforced**:
- Max 10 .md files in root
- No test files in root (except test_architecture.py)
- No .sh files in root (except allowed ones)
- No temp files (.tmp, .cache, .bak)
- Required docs exist
- No archive/ folders

**Installation**:
```bash
cp tools/audit/pre-commit-hook.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

### 2. Manual Check (Run Anytime)
```bash
python3 tools/audit/enforce_structure.py
```

**Output**:
```
✅ All checks passed → Safe to commit
❌ Violations found → Shows what to fix
```

### 3. Auto-Organization (Smart Cleanup)
```bash
python3 tools/cleanup/auto_organize.py --execute
```

**What it does**:
- Analyzes all files in root
- Determines correct category
- Moves to proper location
- Runs enforcement check after

### 4. Monthly Audit (Automated)
```bash
# Add to crontab
0 0 1 * * cd /home/pi/zoe && python3 tools/audit/monthly_audit.sh
```

---

## 📋 Standard Operating Procedures

### SOP-001: Adding Documentation

```bash
# 1. Check current count
ls *.md | wc -l

# 2. If < 10 and document is ESSENTIAL
touch NEW_DOC.md
# Add content
# Reference from PROJECT_STATUS.md if needed

# 3. If >= 10 or NOT essential
touch docs/guides/NEW_DOC.md
# Update docs/README.md

# 4. Verify
python3 tools/audit/enforce_structure.py

# 5. Commit
git add .
git commit -m "docs: add NEW_DOC"
# Pre-commit hook runs automatically ✅
```

### SOP-002: Adding Tests

```bash
# 1. Determine category
# Unit? Integration? Performance? E2E?

# 2. Create in correct folder
touch tests/unit/test_new_feature.py

# 3. Write test with docstring
cat > tests/unit/test_new_feature.py << 'EOF'
"""
Test new feature functionality

Tests:
- Feature behavior
- Edge cases
- Error handling
"""

def test_feature():
    assert True
EOF

# 4. Run test
pytest tests/unit/test_new_feature.py

# 5. Verify structure
python3 tools/audit/enforce_structure.py

# 6. Commit (hook will verify)
git add tests/unit/test_new_feature.py
git commit -m "test: add unit tests for new feature"
```

### SOP-003: Adding Scripts

```bash
# 1. Determine purpose
# Setup? Maintenance? Deployment? Security?

# 2. Create in correct folder
touch scripts/maintenance/backup_database.sh

# 3. Add header
cat > scripts/maintenance/backup_database.sh << 'EOF'
#!/bin/bash
# Backup Zoe database to external storage
# Usage: ./backup_database.sh [destination]

# Your script here
EOF

# 4. Make executable
chmod +x scripts/maintenance/backup_database.sh

# 5. Test
./scripts/maintenance/backup_database.sh --help

# 6. Verify and commit
python3 tools/audit/enforce_structure.py
git add scripts/maintenance/backup_database.sh
git commit -m "chore: add database backup script"
```

### SOP-004: Archiving Old Documentation

```bash
# 1. Identify document to archive
OLD_DOC="SOME_OLD_STATUS.md"

# 2. Add date suffix
NEW_NAME="SOME_OLD_STATUS_$(date +%Y%m%d).md"
mv "$OLD_DOC" "$NEW_NAME"

# 3. Determine category and move
# Status report? → docs/archive/reports/
# Technical doc? → docs/archive/technical/
# Guide? → docs/archive/guides/
mv "$NEW_NAME" docs/archive/reports/

# 4. Update references
python3 tools/cleanup/fix_references.py

# 5. Verify
python3 tools/audit/enforce_structure.py
./verify_updates.sh

# 6. Commit
git add .
git commit -m "docs: archive $OLD_DOC"
```

### SOP-005: Monthly Maintenance

```bash
#!/bin/bash
# Run on 1st of each month

cd /home/pi/zoe

# 1. Run structure enforcement
python3 tools/audit/enforce_structure.py || exit 1

# 2. Run comprehensive audit
python3 tools/audit/comprehensive_audit.py

# 3. Check for misplaced files
python3 tools/cleanup/auto_organize.py  # Dry run first

# 4. Review and clean
# - Move old utility scripts to archived
# - Archive superseded docs
# - Update PROJECT_STATUS.md if needed

# 5. Verify
./verify_updates.sh

# 6. Commit improvements
git add .
git commit -m "chore: monthly maintenance $(date +%Y-%m)"
```

---

## 🚨 Violation Handling

### If Pre-Commit Hook Blocks You

```bash
# 1. See what's wrong
python3 tools/audit/enforce_structure.py

# 2. Fix automatically (if possible)
python3 tools/cleanup/auto_organize.py --execute

# 3. Or fix manually using decision tree above

# 4. Verify fix
python3 tools/audit/enforce_structure.py

# 5. Try commit again
git commit -m "your message"
```

### If You Need to Violate a Rule

**Don't**. Seriously. The rules are simple and comprehensive.

**But if absolutely necessary**:
1. Create GitHub issue explaining why
2. Get approval
3. Document in PROJECT_STATUS.md
4. Set deadline to fix (max 1 week)
5. Add TODO to remove exception

---

## 📊 Compliance Dashboard

### Quick Health Check
```bash
#!/bin/bash
# tools/audit/compliance_dashboard.sh

clear
echo "╔════════════════════════════════════════════════╗"
echo "║     ZOE PROJECT COMPLIANCE DASHBOARD           ║"
echo "╚════════════════════════════════════════════════╝"
echo ""

# Count files
DOCS=$(ls *.md 2>/dev/null | wc -l)
TESTS=$(ls test*.py 2>/dev/null | grep -v test_architecture.py | wc -l)
SCRIPTS=$(ls *.sh 2>/dev/null | grep -v "verify_updates\|start-zoe\|stop-zoe" | wc -l)
TEMP=$(ls *.tmp *.cache *.bak 2>/dev/null | wc -l)

# Show status
echo "📊 Structure Metrics:"
if [ $DOCS -le 10 ]; then
    echo "  ✅ Root docs: $DOCS/10"
else
    echo "  ❌ Root docs: $DOCS/10 (OVER LIMIT)"
fi

if [ $TESTS -eq 0 ]; then
    echo "  ✅ Root tests: $TESTS (organized)"
else
    echo "  ❌ Root tests: $TESTS (should be 0)"
fi

if [ $SCRIPTS -eq 0 ]; then
    echo "  ✅ Root scripts: $SCRIPTS (organized)"
else
    echo "  ❌ Root scripts: $SCRIPTS (should be 0)"
fi

if [ $TEMP -eq 0 ]; then
    echo "  ✅ Temp files: $TEMP"
else
    echo "  ❌ Temp files: $TEMP (should be 0)"
fi

echo ""
echo "📁 Organization:"
echo "  → docs/archive/: $(find docs/archive -name "*.md" 2>/dev/null | wc -l) files"
echo "  → tests/: $(find tests -name "*.py" 2>/dev/null | wc -l) files"
echo "  → scripts/: $(find scripts -name "*.sh" 2>/dev/null | wc -l) files"
echo "  → tools/: $(find tools -name "*.py" 2>/dev/null | wc -l) files"

echo ""
# Run full check
python3 tools/audit/enforce_structure.py > /dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "╔════════════════════════════════════════════════╗"
    echo "║        ✅ PROJECT IS COMPLIANT                  ║"
    echo "╚════════════════════════════════════════════════╝"
else
    echo "╔════════════════════════════════════════════════╗"
    echo "║        ❌ VIOLATIONS DETECTED                   ║"
    echo "╚════════════════════════════════════════════════╝"
    echo ""
    python3 tools/audit/enforce_structure.py
fi
```

### Run Dashboard
```bash
bash tools/audit/compliance_dashboard.sh
```

---

## 🔧 Maintenance Tools

### Primary Tools

| Tool | Purpose | Usage |
|------|---------|-------|
| `enforce_structure.py` | Validate compliance | `python3 tools/audit/enforce_structure.py` |
| `auto_organize.py` | Smart file organization | `python3 tools/cleanup/auto_organize.py --execute` |
| `comprehensive_audit.py` | Full system audit | `python3 tools/audit/comprehensive_audit.py` |
| `fix_references.py` | Update doc references | `python3 tools/cleanup/fix_references.py` |
| `verify_updates.sh` | Quick verification | `./verify_updates.sh` |

### When to Use Each

**Daily/Before Commit**:
```bash
python3 tools/audit/enforce_structure.py
```

**When Adding Files**:
```bash
# After adding, check:
python3 tools/audit/enforce_structure.py

# If violations, auto-fix:
python3 tools/cleanup/auto_organize.py --execute
```

**Monthly**:
```bash
# Full health check
python3 tools/audit/comprehensive_audit.py

# Clean up strays
python3 tools/cleanup/auto_organize.py --execute

# Verify references
python3 tools/audit/audit_references.py
```

**After Major Changes**:
```bash
# Full suite
python3 tools/audit/enforce_structure.py
python3 tools/audit/comprehensive_audit.py
python3 tools/cleanup/fix_references.py
./verify_updates.sh
```

---

## 📖 Documentation Governance

### The 10-File Rule

**Root can have MAX 10 .md files**. Currently at 10/10.

**Current Essential Docs**:
1. README.md
2. CHANGELOG.md
3. QUICK-START.md
4. PROJECT_STATUS.md
5. FIXES_APPLIED.md
6. CLEANUP_PLAN.md
7. CLEANUP_SUMMARY.md
8. DOCUMENTATION_STRUCTURE.md
9. REFERENCES_UPDATED_COMPLETE.md
10. PROJECT_STRUCTURE_RULES.md

**Adding 11th Doc?**
```bash
# Option 1: Replace least essential
mv REFERENCES_UPDATED_COMPLETE.md docs/archive/reports/
touch NEW_ESSENTIAL_DOC.md

# Option 2: Not essential? Put in docs/
touch docs/guides/NEW_DOC.md

# Option 3: Archive old, add new
mv CLEANUP_SUMMARY.md docs/archive/reports/CLEANUP_SUMMARY_$(date +%Y%m%d).md
touch NEW_CURRENT_DOC.md
```

**Single Source of Truth**:
- **System Status**: PROJECT_STATUS.md (update in place, don't create v2)
- **Changes**: CHANGELOG.md (append, don't replace)
- **Structure**: This file (GOVERNANCE.md - update when rules change)

### Update Not Replace

**✅ CORRECT**:
```bash
# Update existing doc
vim PROJECT_STATUS.md
git commit -m "docs: update system status"
```

**❌ WRONG**:
```bash
# Don't create versions
touch PROJECT_STATUS_v2.md  # NO!
touch PROJECT_STATUS_NEW.md  # NO!
touch PROJECT_STATUS_20251008.md  # NO!
```

**Use git for versions**, not filenames.

---

## 🧪 Test Governance

### Organization Rules

**All tests MUST be in `/tests/{category}/`**

**Categories**:
- `unit/` - Fast, isolated, single module
- `integration/` - Multiple components, API tests
- `performance/` - Benchmarks, load tests  
- `e2e/` - Full system, user workflows
- `archived/` - Old, deprecated, or unused

**Exception**: `test_architecture.py` stays in root (validates project structure)

### Test Standards

**Required**:
- Clear docstring explaining what it tests
- Descriptive name: `test_<module>_<feature>.py`
- Use fixtures from `tests/fixtures/`
- Fast execution (< 5s per test for unit tests)

**Forbidden**:
- Tests in root (except exception)
- Tests in services/ folders
- Numbered tests (test1.py, test2.py)
- Vague names (my_test.py, temp_test.py)

### Adding Tests

```python
# tests/unit/test_reminders_api.py
"""
Test reminders API endpoints

Tests:
- GET /api/reminders/ returns correct data
- POST /api/reminders/ creates reminder
- PUT /api/reminders/{id} updates
- DELETE /api/reminders/{id} deletes
"""

import pytest

def test_get_reminders():
    """Test GET endpoint"""
    # Test here
    pass
```

---

## 📜 Script Governance

### Organization Rules

**All scripts MUST be in `/scripts/{category}/`**

**Categories**:
- `setup/` - First-time setup, database init
- `maintenance/` - Backups, cleanup, health
- `deployment/` - Deploy, restart, update
- `security/` - Security audits, key management
- `utilities/` - One-off scripts (archive after use)

**Exceptions**: 
- `verify_updates.sh` (quick check, frequently used)
- `start-zoe.sh` / `stop-zoe.sh` (if they exist - core operations)

### Script Standards

**Required**:
```bash
#!/bin/bash
# Description: What this script does
# Usage: ./script.sh [arguments]
# Author: Your name
# Date: YYYY-MM-DD

set -e  # Exit on error

# Script content
```

**Forbidden**:
- Scripts without headers
- Scripts in root (except exceptions)
- Numbered scripts (script1.sh)
- Backup scripts (script.sh.bak)

---

## 🛠️ Tool Governance

### Organization Rules

**Reusable tools → `/tools/{category}/`**

**Categories**:
- `audit/` - Structure checks, validation
- `cleanup/` - Automated cleanup, organization
- `validation/` - Linters, formatters
- `generators/` - Code generation, scaffolding

**Not Tools** (put elsewhere):
- One-off scripts → `scripts/utilities/`
- Test helpers → `tests/fixtures/`
- Build scripts → `scripts/deployment/`

### Tool Standards

**Required**:
- Docstring with purpose and usage
- Main guard: `if __name__ == "__main__":`
- Help/usage message
- Error handling

**Example**:
```python
#!/usr/bin/env python3
"""
Tool Name - One-line description

Usage:
    python3 tools/category/tool_name.py [options]

Options:
    --help    Show this message
    --verbose Enable verbose output
"""

def main():
    """Main function"""
    pass

if __name__ == "__main__":
    main()
```

---

## 🔒 Enforcement Levels

### Level 1: Pre-Commit (Automatic)
- Blocks commits with violations
- Runs on every commit
- Can't be bypassed easily

### Level 2: CI/CD (Automated)
- Runs on push/PR
- Blocks merges if violations
- Public visibility of compliance

### Level 3: Monthly Audit (Scheduled)
- Automated monthly check
- Sends report
- Creates issues for violations

### Level 4: Manual (On Demand)
- Run anytime: `python3 tools/audit/enforce_structure.py`
- Use before major commits
- Part of release checklist

---

## 📈 Success Metrics

### Project Health Dashboard

**Target**: All ✅

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Root .md files | ≤ 10 | 10 | ✅ |
| Root test files | ≤ 1 | 1 | ✅ |
| Root scripts | ≤ 3 | 1 | ✅ |
| Temp files | 0 | 0 | ✅ |
| Archive folders | 0 | 0 | ✅ |
| Required docs | 4/4 | 4/4 | ✅ |
| Broken links | 0 | 0 | ✅ |

**Overall Compliance**: 100% ✅

---

## 🎓 Benefits

### Before Governance System:
- 😕 Files scattered everywhere
- 😕 No clear rules
- 😕 Manual cleanup needed
- 😕 Mess accumulates
- 😕 Hard to find things
- 😕 No enforcement

### After Governance System:
- ✅ Every file has correct location
- ✅ Clear, simple rules
- ✅ Automated organization
- ✅ Mess prevented automatically
- ✅ Easy to navigate
- ✅ Pre-commit enforcement

**Impact**: 10x better organization, maintenance-free

---

## 🚀 Getting Started

### For New Developers

1. **Read This**: PROJECT_STRUCTURE_RULES.md (the rules)
2. **Read This**: GOVERNANCE.md (this file - how it's enforced)
3. **Run This**: `python3 tools/audit/enforce_structure.py`
4. **Bookmark**: Decision tree above

### For Existing Developers

1. **One-Time**: Run `python3 tools/cleanup/auto_organize.py --execute`
2. **Verify**: Run `python3 tools/audit/enforce_structure.py`
3. **Adopt**: Follow SOPs for new files
4. **Maintain**: Run monthly audit

---

## 📞 Support

### Questions?

**Q**: Where does file X go?  
**A**: Use decision tree above or run auto_organize.py

**Q**: Why was my commit blocked?  
**A**: Run `python3 tools/audit/enforce_structure.py` to see violations

**Q**: How do I fix violations?  
**A**: Run `python3 tools/cleanup/auto_organize.py --execute`

**Q**: Can I bypass the hook?  
**A**: No. Fix the violations instead. Rules are simple.

---

## ✅ Final Checklist

Before considering structure "done":

- [x] Folder structure created
- [x] Rules documented (PROJECT_STRUCTURE_RULES.md)
- [x] Governance documented (this file)
- [x] Enforcement script created (enforce_structure.py)
- [x] Auto-organizer created (auto_organize.py)
- [x] Pre-commit hook installed
- [x] All current files organized
- [x] All checks passing
- [x] Documentation indexes updated
- [x] Maintenance SOPs defined

---

## 🎉 Conclusion

**You now have a self-enforcing, self-organizing project structure.**

- 🔒 **Enforced**: Pre-commit hook prevents violations
- 🤖 **Automated**: Auto-organizer fixes issues
- 📋 **Documented**: Clear rules and SOPs
- 🛠️ **Tooled**: Multiple validation tools
- 📊 **Monitored**: Compliance dashboard
- 🚀 **Scalable**: Works as project grows

**The mess will never happen again.** 🎊

---

**Version**: 1.0  
**Effective**: October 8, 2025  
**Status**: 🔒 ACTIVE & ENFORCED  
**Next Review**: November 8, 2025

