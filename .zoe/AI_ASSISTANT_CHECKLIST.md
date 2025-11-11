# AI Assistant Pre-Action Checklist

**Purpose**: Ensure AI assistants follow all project rules before making changes

## 🔍 BEFORE ANY FILE OPERATION

### Step 1: Check Documentation Location
- [ ] Is this a new documentation file?
  - [ ] If YES → Place in `docs/{category}/` (NOT root)
  - [ ] Root .md limit: MAX 10 files (currently 6/10)
  - [ ] Check: `ls -1 *.md | wc -l` must be ≤ 10

### Step 2: Check Manifest
- [ ] File in `.zoe/manifest.json`?
  - [ ] If YES → Safe to modify
  - [ ] If NO → ASK USER before proceeding

### Step 3: Validate Structure
- [ ] Run: `python3 tools/audit/validate_structure.py`
- [ ] Exit code must be 0 (success)
- [ ] If fails → Fix issues before proceeding

### Step 4: Check Critical Files
- [ ] Is this a critical file? (see `docs/governance/CRITICAL_FILES.md`)
  - [ ] If YES → EXTRA CAUTION required
  - [ ] Run: `python3 tools/audit/validate_critical_files.py`
  - [ ] Check references: `bash tools/audit/find_file_references.sh <file>`

## 📝 BEFORE CREATING NEW FILES

### Documentation Files
- [ ] Place in `docs/{category}/`:
  - Architecture → `docs/architecture/`
  - Guides → `docs/guides/`
  - Governance → `docs/governance/`
  - API → `docs/api/`
- [ ] NEVER create .md files in root (unless approved)
- [ ] Check root count: `ls -1 *.md | wc -l` ≤ 10

### Code Files
- [ ] Follow structure rules:
  - Tests → `tests/{unit|integration|performance|e2e}/`
  - Scripts → `scripts/{setup|maintenance|deployment|security|utilities}/`
  - Tools → `tools/{audit|cleanup|validation|generators}/`

### Configuration Files
- [ ] Check if file should be in root or `config/`
- [ ] Never create prohibited patterns:
  - `*_backup.*`, `*_old.*`, `*_v2.*`, `*_new.*`, `*_fixed.*`
  - `test_*.py` in root
  - `*.log`, `*.tmp` in repo

## 🗑️ BEFORE DELETING FILES

### Mandatory Steps:
1. [ ] Check manifest: `.zoe/manifest.json`
2. [ ] Run: `python3 tools/audit/validate_critical_files.py`
3. [ ] Run: `bash tools/audit/find_file_references.sh <file>`
4. [ ] Create safety commit: `git commit -am "Pre-deletion safety"`
5. [ ] Work in branch: `git checkout -b cleanup-$(date +%Y%m%d)`
6. [ ] Delete max 5-10 files at a time
7. [ ] Test after each deletion
8. [ ] Validate: `python3 tools/audit/validate_structure.py`

### Never Delete:
- [ ] Critical files (see `docs/governance/CRITICAL_FILES.md`)
- [ ] Files in manifest `critical_files` section
- [ ] Files with active references

## 🔧 BEFORE CODE CHANGES

### Architecture Rules
- [ ] Single source of truth (no duplicates)
- [ ] Use intelligent systems (don't replace them)
- [ ] No hardcoding (use LLM + Agents)
- [ ] Check: `PROJECT_STRUCTURE_RULES.md`

### Security Rules
- [ ] No hardcoded API keys/secrets
- [ ] Use environment variables
- [ ] Validate user inputs
- [ ] Parameterized database queries

### Database Rules
- [ ] Use `os.getenv("DATABASE_PATH")` (not hardcoded paths)
- [ ] All endpoints require authentication
- [ ] User isolation enforced

## ✅ AFTER MAKING CHANGES

### Validation Steps:
1. [ ] Run: `python3 tools/audit/validate_structure.py`
2. [ ] Check root .md count: `ls -1 *.md | wc -l` ≤ 10
3. [ ] Verify no prohibited patterns created
4. [ ] Test functionality (if applicable)
5. [ ] Update documentation if needed

### Documentation Updates:
- [ ] If creating new feature → Document in `docs/`
- [ ] If changing architecture → Update `docs/architecture/`
- [ ] If changing rules → Update `.cursorrules` and relevant docs

## 📋 QUICK REFERENCE

### Root .md Files (MAX 10):
1. README.md
2. CHANGELOG.md
3. QUICK-START.md
4. PROJECT_STATUS.md
5. PROJECT_STRUCTURE_RULES.md
6. DATABASE_PROTECTION_RULES.md
7-10. (Available slots)

### Documentation Locations:
- Architecture: `docs/architecture/`
- Guides: `docs/guides/`
- Governance: `docs/governance/`
- API: `docs/api/`
- Archive: `docs/archive/`

### Critical Commands:
```bash
# Validate structure
python3 tools/audit/validate_structure.py

# Check critical files
python3 tools/audit/validate_critical_files.py

# Find file references
bash tools/audit/find_file_references.sh <file>

# Count root .md files
ls -1 *.md | wc -l
```

## 🚨 RED FLAGS (STOP IMMEDIATELY)

- Root .md count > 10
- Creating files in root without approval
- Deleting critical files
- Structure validation fails
- Creating prohibited patterns
- Hardcoding database paths
- Removing authentication from endpoints

## 📖 REFERENCE DOCS

- `.cursorrules` - Main rules file
- `PROJECT_STRUCTURE_RULES.md` - Structure rules
- `docs/governance/CLEANUP_SAFETY.md` - Cleanup safety
- `docs/governance/CRITICAL_FILES.md` - Critical files list
- `docs/governance/MANIFEST_SYSTEM.md` - Manifest system
- `.zoe/manifest.json` - Approved files registry

---

**Last Updated**: November 7, 2025  
**Status**: ✅ Active




