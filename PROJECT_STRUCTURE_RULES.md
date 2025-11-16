# 🏗️ Zoe Project Structure & Governance Rules

**Version**: 1.0  
**Date**: October 8, 2025  
**Status**: 🔒 ENFORCED

This document defines the **mandatory** structure and rules for the Zoe project. All files must follow these rules. Automated checks enforce compliance.

---

## 🎯 Core Principle

**ONE RULE**: Every file has exactly ONE correct location based on its purpose.

---

## 🗄️ DATABASE PATH RULES - CRITICAL

### ⚠️ MANDATORY: Use Environment Variables for Database Paths

**PROBLEM**: Docker containers map paths differently:
- Host: `/home/zoe/assistant/data/zoe.db`
- Container: `/app/data/zoe.db`

**SOLUTION**: Always use `os.getenv("DATABASE_PATH")`

### ✅ CORRECT Pattern:
```python
import os

def __init__(self, db_path: str = None):
    if db_path is None:
        db_path = os.getenv("DATABASE_PATH", "/home/zoe/assistant/data/zoe.db")
    self.db_path = db_path
```

### ❌ WRONG Pattern (Will Break in Docker):
```python
def __init__(self, db_path: str = "/home/zoe/assistant/data/zoe.db"):  # HARDCODED!
```

### 🔒 Enforcement:
- **Pre-commit hook** runs `tools/audit/check_database_paths.py`
- **Blocks commits** with hardcoded database paths
- **Run manually**: `python3 tools/audit/check_database_paths.py`

### 📋 Affected Files:
- All services in `services/zoe-core/services/`
- All routers in `services/zoe-core/routers/`
- Any new code that accesses databases

---

## 🔒 AUTHENTICATION & USER ISOLATION RULES - CRITICAL

### ⚠️ MANDATORY: All User Data Endpoints Must Require Authentication

**PROBLEM**: Endpoints with `user_id = Query("default")` or `Query(None)` allow:
- Unauthorized access to user data
- Users accessing other users' data
- Complete bypass of authentication

**SOLUTION**: Always use `session: AuthenticatedSession = Depends(validate_session)`

### ✅ CORRECT Pattern:
```python
from fastapi import APIRouter, Depends
from auth import AuthenticatedSession, validate_session

@router.get("/data")
async def get_user_data(
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get data for authenticated user"""
    user_id = session.user_id
    # ... use user_id for database queries
```

### ❌ WRONG Patterns (Security Vulnerabilities):
```python
# WRONG: Hardcoded default user
@router.get("/data")
async def get_user_data(user_id: str = Query("default")):
    pass

# WRONG: Optional user_id without authentication
@router.get("/data")
async def get_user_data(user_id: str = Query(None)):
    pass

# WRONG: Optional authentication
@router.get("/data")
async def get_user_data(
    user_id: str = Query(None),
    session: Optional[AuthenticatedSession] = Depends(lambda: None)
):
    pass
```

### 🔒 Enforcement:
- **Pre-commit hook** runs `tools/audit/check_authentication.py`
- **Blocks commits** with insecure authentication patterns
- **Run manually**: `python3 tools/audit/check_authentication.py`
- **Auto-fix available**: `python3 scripts/utilities/fix_user_isolation.py`

### 📋 Affected Endpoints:
- **ALL** routers in `services/zoe-core/routers/` that access user data
- Exceptions documented in `tools/audit/check_authentication.py`
- Public endpoints should be explicitly marked as public

### 🎯 Best Practices:
1. **Always extract user_id first**: `user_id = session.user_id`
2. **Never use 'default' user**: No hardcoded user IDs
3. **Database queries must filter by user_id**: `WHERE user_id = ?`
4. **Test with multiple users**: Verify isolation works
5. **Mark exceptions explicitly**: Document why if no auth needed

---

## 📁 Mandatory Folder Structure

```
/home/zoe/assistant/
│
├── 📚 DOCUMENTATION (Root - Max 10 files)
│   ├── README.md                    [REQUIRED] Project overview
│   ├── CHANGELOG.md                 [REQUIRED] Version history
│   ├── QUICK-START.md               [REQUIRED] Getting started
│   ├── PROJECT_STATUS.md            [REQUIRED] Current system state
│   └── [Up to 6 more essential docs]
│
├── 🧪 tests/
│   ├── unit/                        Unit tests
│   ├── integration/                 Integration tests
│   ├── performance/                 Performance tests
│   ├── e2e/                         End-to-end tests
│   ├── fixtures/                    Test data/fixtures
│   └── archived/                    Old/deprecated tests
│   └── [NO test files in root!]
│
├── 📜 scripts/
│   ├── setup/                       Setup & installation scripts
│   ├── maintenance/                 Maintenance scripts
│   ├── deployment/                  Deployment scripts
│   ├── security/                    Security scripts
│   ├── utilities/                   One-off utility scripts
│   └── [NO scripts in project root!]
│
├── 📖 docs/
│   ├── README.md                    Documentation index
│   ├── guides/                      User & developer guides
│   ├── api/                         API documentation
│   ├── architecture/                Architecture docs
│   └── archive/                     Historical documentation
│       ├── reports/                 Old status reports
│       ├── technical/               Old technical docs
│       └── guides/                  Superseded guides
│
├── 🔧 tools/                        [ALLOWED IN ROOT]
│   ├── audit/                       Audit & validation tools
│   ├── cleanup/                     Cleanup automation
│   └── [automation scripts]
│
├── 🐳 services/                     [DO NOT MODIFY STRUCTURE]
│   ├── zoe-core/
│   ├── zoe-ui/
│   └── ...
│
├── 💾 data/                         [APPLICATION DATA - DO NOT COMMIT]
│   └── zoe.db
│
└── ⚙️ config/                       Configuration files
    └── *.yaml, *.json
```

---

## 🚨 STRICT RULES

### Rule 1: Documentation
```
✅ ALLOWED in root:
  - README.md (required)
  - CHANGELOG.md (required)
  - QUICK-START.md (required)
  - PROJECT_STATUS.md (required)
  - Up to 6 more ESSENTIAL docs

❌ FORBIDDEN in root:
  - Status reports (→ docs/archive/reports/)
  - Technical docs (→ docs/archive/technical/)
  - Integration guides (→ docs/guides/)
  - Completed/done docs (→ docs/archive/reports/)
  - Backup docs (DELETE - use git)
```

**Enforcement**: Max 10 .md files in root. Automated check fails if exceeded.

### Rule 2: Test Files
```
✅ ALLOWED:
  - tests/unit/*.py          - Unit tests
  - tests/integration/*.py   - Integration tests
  - tests/performance/*.py   - Performance tests
  - tests/e2e/*.py          - End-to-end tests
  - test_architecture.py    - EXCEPTION: Keep in root (important)

❌ FORBIDDEN:
  - test*.py in project root (except test_architecture.py)
  - *_test.py in project root
  - Test files scattered in services/
```

**Enforcement**: No test*.py or *_test.py files in root except test_architecture.py.

### Rule 3: Scripts
```
✅ ALLOWED:
  - scripts/setup/*.sh       - Setup scripts
  - scripts/maintenance/*.sh - Maintenance scripts
  - scripts/deployment/*.sh  - Deployment scripts
  - scripts/security/*.sh    - Security scripts
  - scripts/utilities/*.sh   - One-off utilities

❌ FORBIDDEN:
  - *.sh files in project root
  - Random scripts in services/
  - Script files without category
```

**Enforcement**: No .sh files in project root.

### Rule 4: Tools
```
✅ ALLOWED in root OR tools/:
  - Audit tools (comprehensive_audit.py)
  - Cleanup tools (comprehensive_cleanup.py)
  - Validation tools (verify_*.sh)
  - Important utilities

❌ FORBIDDEN:
  - One-off test scripts → tests/archived/
  - Benchmark scripts → tools/benchmarking/
  - Migration scripts → scripts/utilities/ (after use)
```

**Enforcement**: Tools must be reusable, not one-off.

### Rule 5: Temporary Files
```
❌ NEVER COMMIT:
  - *.tmp, *.cache, *.log
  - *_backup.*, *.backup, *.bak
  - .DS_Store, ._*, Thumbs.db
  - test*.tmp, temp_*

✅ ADD TO .gitignore:
  All temp file patterns
```

**Enforcement**: Pre-commit hook blocks these files.

---

## 📋 File Naming Conventions

### Documentation Files
```
✅ GOOD:
  - PROJECT_STATUS.md           (clear, no date)
  - QUICK-START.md             (clear purpose)
  - API_REFERENCE.md           (descriptive)

❌ BAD:
  - STATUS_v2.md               (no versions - use git)
  - CURRENT_STATUS_20251008.md (no dates in filename)
  - NEW_STATUS.md              (vague)
  - STATUS_FINAL_REALLY.md     (multiple versions)
```

### Test Files
```
✅ GOOD:
  - test_api_endpoints.py      (clear what it tests)
  - test_database_schema.py    (specific)
  - integration_test_auth.py   (clear category)

❌ BAD:
  - test1.py                   (numbered)
  - my_test.py                 (vague)
  - temp_test.py               (temp)
  - test_new.py                (vague)
```

### Script Files
```
✅ GOOD:
  - setup_database.sh          (clear purpose)
  - deploy_production.sh       (specific)
  - backup_data.sh            (clear action)

❌ BAD:
  - script1.sh                 (numbered)
  - temp.sh                    (temp)
  - new_script.sh              (vague)
  - fix.sh                     (too vague)
```

---

## 🔍 Decision Tree: Where Does This File Go?

### Is it a Markdown file?
```
├─ Is it essential documentation?
│  ├─ YES → Project root (if < 10 total)
│  └─ NO → docs/
│     ├─ User guide? → docs/guides/
│     ├─ API docs? → docs/api/
│     ├─ Architecture? → docs/architecture/
│     └─ Old/superseded? → docs/archive/{category}/
```

### Is it a test file?
```
├─ Is it test_architecture.py?
│  ├─ YES → Keep in root
│  └─ NO → tests/
│     ├─ Unit test? → tests/unit/
│     ├─ Integration? → tests/integration/
│     ├─ Performance? → tests/performance/
│     ├─ E2E? → tests/e2e/
│     └─ Old/unused? → tests/archived/
```

### Is it a script file?
```
├─ What's its purpose?
│  ├─ Setup → scripts/setup/
│  ├─ Maintenance → scripts/maintenance/
│  ├─ Deployment → scripts/deployment/
│  ├─ Security → scripts/security/
│  ├─ One-off utility → scripts/utilities/
│  └─ NEVER in project root
```

### Is it a Python tool?
```
├─ Is it reusable automation?
│  ├─ YES → tools/{category}/
│  │   ├─ Audit → tools/audit/
│  │   ├─ Cleanup → tools/cleanup/
│  │   └─ Validation → tools/validation/
│  └─ NO → Delete or move to scripts/utilities/
```

### Is it temporary?
```
└─ Delete it. Use .gitignore to prevent commit.
```

---

## ⚙️ Automated Enforcement

### Pre-Commit Checks (`tools/audit/enforce_structure.py`)

```python
#!/usr/bin/env python3
"""
Enforce project structure rules before commit
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path("/home/zoe/assistant")
MAX_ROOT_DOCS = 10

def check_root_md_files():
    """Max 10 .md files in root"""
    md_files = list(PROJECT_ROOT.glob("*.md"))
    if len(md_files) > MAX_ROOT_DOCS:
        print(f"❌ RULE VIOLATION: {len(md_files)} .md files in root (max {MAX_ROOT_DOCS})")
        print("   Move extras to docs/archive/")
        return False
    return True

def check_no_root_tests():
    """No test files in root except test_architecture.py"""
    test_files = [f for f in PROJECT_ROOT.glob("test*.py") 
                  if f.name != "test_architecture.py"]
    if test_files:
        print(f"❌ RULE VIOLATION: {len(test_files)} test files in root")
        print("   Move to tests/{category}/")
        for f in test_files:
            print(f"   - {f.name}")
        return False
    return True

def check_no_root_scripts():
    """No .sh files in root except allowed ones"""
    allowed = ["verify_updates.sh"]
    sh_files = [f for f in PROJECT_ROOT.glob("*.sh") 
                if f.name not in allowed]
    if sh_files:
        print(f"❌ RULE VIOLATION: {len(sh_files)} .sh files in root")
        print("   Move to scripts/{category}/")
        for f in sh_files:
            print(f"   - {f.name}")
        return False
    return True

def check_no_temp_files():
    """No temporary files"""
    patterns = ["*.tmp", "*.cache", "*.bak", "*_backup.*"]
    temp_files = []
    for pattern in patterns:
        temp_files.extend(PROJECT_ROOT.glob(pattern))
    
    if temp_files:
        print(f"❌ RULE VIOLATION: {len(temp_files)} temp files found")
        print("   These should not be committed:")
        for f in temp_files[:10]:
            print(f"   - {f.name}")
        return False
    return True

def check_required_docs():
    """Required documentation must exist"""
    required = ["README.md", "CHANGELOG.md", "QUICK-START.md", "PROJECT_STATUS.md"]
    missing = [doc for doc in required if not (PROJECT_ROOT / doc).exists()]
    
    if missing:
        print(f"❌ RULE VIOLATION: Missing required documentation:")
        for doc in missing:
            print(f"   - {doc}")
        return False
    return True

if __name__ == "__main__":
    print("\n" + "="*60)
    print("PROJECT STRUCTURE ENFORCEMENT")
    print("="*60 + "\n")
    
    checks = [
        ("Max 10 docs in root", check_root_md_files),
        ("No test files in root", check_no_root_tests),
        ("No scripts in root", check_no_root_scripts),
        ("No temp files", check_no_temp_files),
        ("Required docs exist", check_required_docs)
    ]
    
    failed = []
    for name, check_func in checks:
        if not check_func():
            failed.append(name)
        else:
            print(f"✅ {name}")
    
    print("\n" + "="*60)
    if failed:
        print("❌ STRUCTURE VIOLATIONS DETECTED")
        print("="*60)
        print(f"\nFailed checks: {len(failed)}")
        for check in failed:
            print(f"  • {check}")
        print("\n⚠️  Fix violations before committing!")
        sys.exit(1)
    else:
        print("✅ ALL STRUCTURE RULES PASSED")
        print("="*60)
        sys.exit(0)
```

### Monthly Structure Audit
```bash
#!/bin/bash
# Run monthly to ensure structure compliance

python3 tools/audit/enforce_structure.py || exit 1
python3 comprehensive_audit.py
./verify_updates.sh
```

---

## 📋 Governance Rules

### Documentation Rules

#### Root Level (Max 10 Files)
**Purpose**: Essential, frequently accessed documentation only

**Allowed**:
1. `README.md` - Project overview [REQUIRED]
2. `CHANGELOG.md` - Version history [REQUIRED]
3. `QUICK-START.md` - Getting started [REQUIRED]
4. `PROJECT_STATUS.md` - Current system state [REQUIRED]
5. Up to 6 more essential docs (e.g., security guide, contribution guide)

**Process**:
- When adding: Ask "Is this essential AND accessed frequently?"
- If NO → Put in `docs/{category}/`
- When count > 10 → Archive oldest to `docs/archive/`
- Update references using `tools/cleanup/fix_references.py`

#### docs/ Folder
**Purpose**: Organized, categorized documentation

**Structure**:
```
docs/
├── guides/          # User & developer guides
├── api/             # API documentation
├── architecture/    # System architecture
└── archive/         # Historical docs
    ├── reports/     # Old status reports
    ├── technical/   # Old technical docs
    └── guides/      # Superseded guides
```

**Rules**:
- Every doc must have a category
- Archive old versions when updating
- Add date suffix when archiving: `DOC_20251008.md`
- Update `docs/README.md` index

### Test File Rules

#### Structure
```
tests/
├── unit/            # Fast, isolated unit tests
├── integration/     # Multi-component tests
├── performance/     # Benchmark & performance tests
├── e2e/             # Full system tests
├── fixtures/        # Test data, mocks, fixtures
├── archived/        # Deprecated/old tests
└── conftest.py      # Shared pytest configuration
```

**Rules**:
- ✅ ALL test files go in `tests/{category}/`
- ❌ NO test files in project root (except test_architecture.py)
- ✅ Name pattern: `test_<what_it_tests>.py`
- ✅ One test file per module/feature
- ✅ Use fixtures for test data

**Exception**:
- `test_architecture.py` stays in root (important validation)

**Process**:
1. Create test in appropriate category folder
2. Use clear, descriptive name
3. Add docstring explaining what it tests
4. Register in test suite if needed

### Script File Rules

#### Structure
```
scripts/
├── setup/           # Database init, first-time setup
├── maintenance/     # Backups, cleanup, health checks
├── deployment/      # Deploy, update, restart services
├── security/        # Security audits, key management
└── utilities/       # One-off tools (archive after use)
```

**Rules**:
- ✅ ALL .sh files go in `scripts/{category}/`
- ❌ NO .sh files in project root (except verify_updates.sh temporarily)
- ✅ Name pattern: `<action>_<object>.sh` (e.g., `backup_database.sh`)
- ✅ Must have shebang: `#!/bin/bash`
- ✅ Must have description comment at top

**Process**:
1. Determine category (setup/maintenance/deployment/security)
2. Create in appropriate folder
3. Add clear docstring
4. Make executable: `chmod +x`
5. Test before commit

### Tool File Rules

#### Structure
```
tools/
├── audit/           # Structure enforcement, health checks
├── cleanup/         # Cleanup automation
├── validation/      # Validators, linters
└── generators/      # Code generators, scaffolding
```

**Rules**:
- ✅ Reusable Python tools → `tools/{category}/`
- ✅ One-off scripts → `scripts/utilities/` (then archive)
- ✅ Must have docstring and usage instructions
- ❌ Don't duplicate functionality

**Decision**:
- Reusable > 3 times? → tools/
- One-off utility? → scripts/utilities/
- Test automation? → tests/

---

## 🔒 Enforcement Mechanisms

### 1. Pre-Commit Hook
```bash
#!/bin/bash
# .git/hooks/pre-commit

# Run structure enforcement
python3 tools/audit/enforce_structure.py || {
    echo "❌ Structure violations detected. Fix before commit."
    exit 1
}

# Check for forbidden files
git diff --cached --name-only | while read file; do
    case "$file" in
        *.tmp|*.cache|*.bak|*_backup.*)
            echo "❌ Temporary file: $file"
            exit 1
            ;;
        test*.py)
            if [ "$file" != "test_architecture.py" ] && [[ ! "$file" =~ ^tests/ ]]; then
                echo "❌ Test file in wrong location: $file"
                exit 1
            fi
            ;;
    esac
done
```

### 2. CI/CD Check
```yaml
# .github/workflows/structure-check.yml
name: Structure Compliance

on: [push, pull_request]

jobs:
  check-structure:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Check project structure
        run: python3 tools/audit/enforce_structure.py
```

### 3. Monthly Audit
```bash
# Cron job: Run 1st of each month
0 0 1 * * cd /home/zoe/assistant && python3 tools/audit/monthly_audit.sh
```

---

## 📝 Process Workflows

### Adding New Documentation

```bash
# 1. Determine if essential
Is it frequently accessed? Is it core to project?

# 2. Check current count
ls *.md | wc -l

# 3a. If < 10 and essential
touch NEW_DOC.md
# Update PROJECT_STATUS.md to reference it

# 3b. If not essential or count >= 10
mkdir -p docs/guides
touch docs/guides/NEW_DOC.md
# Update docs/README.md

# 4. Verify
python3 tools/audit/enforce_structure.py
```

### Adding New Test

```bash
# 1. Determine category
Unit? Integration? Performance? E2E?

# 2. Create in correct folder
touch tests/unit/test_new_feature.py

# 3. Add docstring
"""Test new feature functionality

This test covers:
- Feature X behavior
- Edge cases for Y
- Error handling
"""

# 4. Verify
python3 tools/audit/enforce_structure.py
pytest tests/unit/test_new_feature.py
```

### Adding New Script

```bash
# 1. Determine category
Setup? Maintenance? Deployment? Security? Utility?

# 2. Create in correct folder
touch scripts/maintenance/backup_database.sh

# 3. Add header
#!/bin/bash
# Backup database to external storage
# Usage: ./backup_database.sh [destination]

# 4. Make executable
chmod +x scripts/maintenance/backup_database.sh

# 5. Verify
python3 tools/audit/enforce_structure.py
```

### Archiving Old Documentation

```bash
# 1. Determine category
Status report? Technical doc? Guide?

# 2. Add date suffix
mv OLD_DOC.md "OLD_DOC_$(date +%Y%m%d).md"

# 3. Move to archive
mv OLD_DOC_20251008.md docs/archive/{category}/

# 4. Update references
python3 tools/cleanup/fix_references.py

# 5. Verify
./verify_updates.sh
```

---

## 🚦 Quality Gates

### Before Commit
```bash
# Run these checks
python3 tools/audit/enforce_structure.py     # Structure rules
python3 audit_references.py                  # No broken links
./verify_updates.sh                          # Verify integrity

# All must pass ✅
```

### Before Push
```bash
# Run full audit
python3 comprehensive_audit.py

# Check for:
# - API errors
# - Schema mismatches
# - Broken UI pages
```

### Monthly
```bash
# Full cleanup audit
python3 comprehensive_cleanup.py

# Review and clean up
# - Old utility scripts
# - Unused test files
# - Superseded documentation
```

---

## 📊 Compliance Monitoring

### Key Metrics to Track

| Metric | Target | Check Command |
|--------|--------|---------------|
| Root .md files | ≤ 10 | `ls *.md \| wc -l` |
| Root test files | ≤ 1 | `ls test*.py \| wc -l` |
| Root scripts | ≤ 2 | `ls *.sh \| wc -l` |
| Temp files | = 0 | `ls *.tmp *.cache \| wc -l` |
| Broken links | = 0 | `python3 audit_references.py` |

### Dashboard
```bash
# Quick compliance check
cat tools/audit/compliance_status.sh

# Output:
# ✅ Documentation: 9/10
# ✅ Tests: Organized
# ✅ Scripts: Organized
# ✅ No temp files
# ✅ No broken links
```

---

## 🎓 Training & Onboarding

### For New Developers

**Required Reading**:
1. This document (PROJECT_STRUCTURE_RULES.md)
2. DOCUMENTATION_STRUCTURE.md
3. Run: `python3 tools/audit/enforce_structure.py`

**Key Lessons**:
- Every file has ONE correct location
- Use the decision tree above
- Run enforcement before commit
- Keep root clean (max 10 docs)

### Quick Reference Card

```
📄 Documentation → Root (if essential) or docs/{category}/
🧪 Test Files    → tests/{unit|integration|performance|e2e}/
📜 Scripts       → scripts/{setup|maintenance|deployment|security}/
🛠️ Tools         → tools/{audit|cleanup|validation}/
🗑️ Temporary     → DELETE (add to .gitignore)
📦 Archive       → docs/archive/{category}/ (when superseded)
```

---

## 🔄 Migration Path (One-Time)

### Step 1: Move Remaining Files
```bash
# Move remaining docs
python3 final_cleanup.py --execute

# Move remaining tests  
mv test*.py tests/archived/ 2>/dev/null
mv *_test.py tests/archived/ 2>/dev/null

# Move remaining scripts
mv *.sh scripts/utilities/ 2>/dev/null

# Keep exceptions
mv tests/archived/test_architecture.py .
mv scripts/utilities/verify_updates.sh .
```

### Step 2: Install Enforcement
```bash
# Create tools structure
mkdir -p tools/{audit,cleanup,validation}

# Move audit tools
mv comprehensive_audit.py tools/audit/
mv audit_references.py tools/audit/
mv enforce_structure.py tools/audit/

# Move cleanup tools
mv comprehensive_cleanup.py tools/cleanup/
mv fix_references.py tools/cleanup/
mv final_cleanup.py tools/cleanup/

# Create pre-commit hook
cp tools/audit/pre-commit-hook.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

### Step 3: Verify
```bash
# Run enforcement
python3 tools/audit/enforce_structure.py

# Should show: ✅ ALL RULES PASSED
```

---

## ⚖️ Exceptions & Special Cases

### Allowed in Root
```
✅ Essential docs (max 10)
✅ test_architecture.py (important validation)
✅ verify_updates.sh (quick verification)
✅ .gitignore, docker-compose.yml, etc. (config files)
```

### Temporary Exceptions
If you need to temporarily violate a rule:

1. Add comment explaining why
2. Create issue to fix
3. Set deadline (max 1 week)
4. Add to PROJECT_STATUS.md known issues

---

## 🎯 Success Criteria

Structure is compliant when:

- ✅ Root has ≤ 10 .md files
- ✅ Root has ≤ 1 test file (test_architecture.py)
- ✅ Root has ≤ 2 .sh files (verify_updates.sh, maybe 1 more)
- ✅ All tests in tests/{category}/
- ✅ All scripts in scripts/{category}/
- ✅ All tools in tools/{category}/
- ✅ No temp files anywhere
- ✅ No archive folders (use docs/archive/)
- ✅ Enforcement script passes

---

## 🚀 Benefits of This System

### For Developers
- Know exactly where to put files
- Easy to find what you need
- Clear rules, no ambiguity
- Automated validation

### For Project
- Maintainable structure
- Scales with growth
- Professional organization
- Easy onboarding

### For Maintenance
- Automated enforcement
- Clear processes
- Self-documenting
- Prevents mess

---

## 📞 Questions?

**Q**: Where do I put a new test file?  
**A**: Use decision tree above → `tests/{unit|integration|performance|e2e}/`

**Q**: Root has 10 docs, need to add another?  
**A**: Archive least important to `docs/archive/`, then add new one

**Q**: Created a utility script, where does it go?  
**A**: `scripts/utilities/` initially, archive after one-time use

**Q**: How do I know if I'm compliant?  
**A**: Run `python3 tools/audit/enforce_structure.py` AND `python3 tools/audit/validate_databases.py`

**Q**: Can I create a new database for my feature?  
**A**: NO! Use zoe.db. Only exception is memory.db for Light RAG. See Database Rules above.

**Q**: I found code using auth.db or developer_tasks.db, is that wrong?  
**A**: YES! These were consolidated. Update to use zoe.db immediately.

**Q**: What if validation fails before commit?  
**A**: Remove forbidden databases, update code to use zoe.db, then re-run validation

---

## 🔄 Git Commit Standards (NEW in v2.4.0)

### Conventional Commits (ENFORCED)

All commit messages MUST follow Conventional Commits format:

```
type(scope): description

[optional body]
[optional footer]
```

**Valid Types**:
- `feat` - New feature
- `fix` - Bug fix  
- `db` - Database changes
- `docs` - Documentation
- `refactor` - Code refactoring
- `perf` - Performance improvement
- `test` - Tests
- `build` - Build system
- `ci` - CI/CD
- `chore` - Maintenance
- `style` - Code formatting

**Examples**:
```
feat(chat): Add auto-discovery router system
fix(auth): Database configuration for multi-user
db: Upgrade to v2.3.1 with connection pooling
docs: Archive completion reports
```

**Enforcement**: commit-msg hook validates format before commit is created.

**See**: `/docs/guides/CHANGE_MANAGEMENT.md` for complete guide

### Automated CHANGELOG

CHANGELOG.md is auto-generated from conventional commits:

```bash
# Generate CHANGELOG for new version
python3 tools/generators/generate_changelog.py --version v2.4.0
```

**Benefits**:
- Automatic version history
- Clear categorization of changes
- Easy to see what changed between versions

### Weekly Change Summaries

Track what changed each week:

```bash
# See last week's changes
./tools/reports/weekly_summary.sh

# See last 2 weeks
./tools/reports/weekly_summary.sh 2
```

### Repository Health Check

Monitor project organization:

```bash
python3 tools/reports/repo_health.py
```

Shows: repo size, file counts, structure compliance, recent activity.

---

## 💾 Database Management (NEW in v2.4.0)

### Schema-Only Approach

**DO**:
- ✅ Track schema files in `data/schema/*.sql`
- ✅ Use `./scripts/setup/init_databases.sh` for fresh installs
- ✅ Run `./scripts/maintenance/export_schema.sh` after schema changes

**DON'T**:
- ❌ Track database files (`data/*.db`) in git
- ❌ Commit your personal data
- ❌ Check in database backups

**Migration**: See `/docs/guides/MIGRATION_TO_V2.4.md`

---

**This is the law of the land. Follow these rules to keep Zoe clean!** 🏛️

*Effective immediately. Enforced automatically. No exceptions without approval.*

