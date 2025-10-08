# 📚 Zoe Documentation Structure Guide

**Last Updated**: October 8, 2025  
**Purpose**: Define canonical documentation structure and what to reference

---

## 🎯 Quick Reference

### Where Is Everything?

| You Need | Look Here | File |
|----------|-----------|------|
| Project Overview | Root | `README.md` |
| Getting Started | Root | `QUICK-START.md` |
| **Current System Status** | **Root** | **`PROJECT_STATUS.md`** ⭐ |
| Recent Bug Fixes | Root | `FIXES_APPLIED.md` |
| Maintenance Guide | Root | `CLEANUP_PLAN.md` |
| Cleanup Report | Root | `CLEANUP_SUMMARY.md` |
| Version History | Root | `CHANGELOG.md` |
| Old Status Docs | Archive | `docs/archive/reports/` |
| Old Technical Docs | Archive | `docs/archive/technical/` |
| Old Guides | Archive | `docs/archive/guides/` |

---

## 📁 Canonical Structure

```
/home/pi/zoe/
│
├── 📚 CORE DOCUMENTATION (Root Level)
│   ├── README.md                    ⭐ Start here
│   ├── QUICK-START.md               ⭐ How to use
│   ├── PROJECT_STATUS.md            ⭐ SINGLE SOURCE OF TRUTH
│   ├── CHANGELOG.md                 
│   ├── FIXES_APPLIED.md             
│   ├── CLEANUP_PLAN.md              
│   └── CLEANUP_SUMMARY.md           
│
├── 📁 docs/
│   ├── README.md                    → Documentation index
│   │
│   ├── archive/
│   │   ├── reports/                 → Old status reports
│   │   ├── technical/               → Old technical docs
│   │   └── guides/                  → Old integration guides
│   │
│   ├── guides/                      → Future user guides
│   └── api/                         → Future API docs
│
├── 📁 services/                     → Service code
│   ├── zoe-core/                    
│   ├── zoe-ui/                      
│   └── ...
│
├── 🛠️ TOOLS
│   ├── comprehensive_audit.py       → System health check
│   ├── comprehensive_cleanup.py     → Cleanup automation
│   ├── consolidate_docs.py          → Doc organization
│   ├── audit_references.py          → Find old references
│   └── fix_references.py            → Update references
│
└── 📊 data/                         → Application data
```

---

## ⭐ Single Sources of Truth

### For System Status
**USE**: `PROJECT_STATUS.md`  
**DON'T USE**: ZOES_CURRENT_STATE.md, SYSTEM_STATUS.md, FINAL_STATUS_REPORT.md

**Why**: All old status docs were consolidated into PROJECT_STATUS.md  
**Location**: Project root  
**Updated**: When major changes occur

### For Cleanup Information
**USE**: `CLEANUP_SUMMARY.md`  
**DON'T USE**: CLEANUP_COMPLETE_SUMMARY.md

**Why**: Renamed during cleanup for clarity  
**Location**: Project root  
**Updated**: After cleanup activities

### For Recent Fixes
**USE**: `FIXES_APPLIED.md`  
**DON'T USE**: Various *_FIX*.md files

**Why**: Centralized bug fix documentation  
**Location**: Project root  
**Updated**: When bugs are fixed

---

## 🔍 For Developers & Scripts

### How to Reference Documentation in Code

```python
# ✅ CORRECT - Use canonical files
PROJECT_ROOT = Path("/home/pi/zoe")
status_doc = PROJECT_ROOT / "PROJECT_STATUS.md"
cleanup_doc = PROJECT_ROOT / "CLEANUP_SUMMARY.md"

# ❌ WRONG - Don't reference old files
old_status = PROJECT_ROOT / "ZOES_CURRENT_STATE.md"  # MOVED!
old_cleanup = PROJECT_ROOT / "CLEANUP_COMPLETE_SUMMARY.md"  # RENAMED!
```

### How to Find Archived Documentation

```python
# Archived status reports
archive_path = PROJECT_ROOT / "docs/archive/reports"

# Archived technical docs  
archive_path = PROJECT_ROOT / "docs/archive/technical"

# Archived guides
archive_path = PROJECT_ROOT / "docs/archive/guides"
```

### How to Add New Documentation

```python
# 1. Current/Active docs → Project root
new_doc = PROJECT_ROOT / "NEW_FEATURE_GUIDE.md"

# 2. When superseded → Archive with date
old_doc = PROJECT_ROOT / "docs/archive/guides/NEW_FEATURE_GUIDE_20251008.md"

# 3. Update PROJECT_STATUS.md to reference new doc
```

---

## 📋 Documentation Categories

### 1. **Essential** (Never Move)
- `README.md` - Project overview
- `CHANGELOG.md` - Version history  
- `QUICK-START.md` - Getting started

**Rule**: These are permanent project fixtures

### 2. **Current Status** (Update In Place)
- `PROJECT_STATUS.md` - System status
- `FIXES_APPLIED.md` - Recent fixes

**Rule**: Update these files, don't create new versions

### 3. **Reports** (Archive When Superseded)
- Cleanup reports
- Test reports  
- System audits

**Rule**: Keep current in root, archive old versions

### 4. **Historical** (In Archive)
- Old status docs
- Phase completion docs
- Deprecated guides

**Rule**: Reference only for historical context

---

## 🔄 Lifecycle of Documentation

### New Document Created
```
1. Create in project root
2. Name clearly: FEATURE_NAME_GUIDE.md
3. Reference from README or PROJECT_STATUS
```

### Document Updated
```
1. Update in place (don't create v2)
2. Use git for version history
3. Update "Last Updated" date
```

### Document Superseded
```
1. Move to docs/archive/{category}/
2. Add date suffix: DOCUMENT_20251008.md
3. Update all references to new doc
4. Document move in CHANGELOG
```

### Document Deprecated
```
1. Move to docs/archive/{category}/
2. Add deprecation note at top
3. Point to replacement doc
4. Keep for 6 months, then consider deletion
```

---

## 🤖 For Automated Processes

### CI/CD Scripts Should Reference

```bash
# Health check
python3 /home/pi/zoe/comprehensive_audit.py

# Current status
cat /home/pi/zoe/PROJECT_STATUS.md

# Version info
grep "^##" /home/pi/zoe/CHANGELOG.md | head -5
```

### Build Scripts Should Check

```bash
# Essential docs exist
test -f README.md
test -f QUICK-START.md
test -f PROJECT_STATUS.md

# Documentation structure
test -d docs/archive
test -f docs/README.md
```

### Documentation Tools Should Look For

```python
CORE_DOCS = [
    "README.md",
    "CHANGELOG.md", 
    "QUICK-START.md",
    "PROJECT_STATUS.md",
    "FIXES_APPLIED.md",
    "CLEANUP_PLAN.md",
    "CLEANUP_SUMMARY.md"
]

ARCHIVE_DIRS = [
    "docs/archive/reports",
    "docs/archive/technical",
    "docs/archive/guides"
]
```

---

## 🔗 Link Guidelines

### In Markdown Files

```markdown
# ✅ CORRECT - Relative links to current docs
See [PROJECT_STATUS.md](PROJECT_STATUS.md) for details.
See [Getting Started](QUICK-START.md).

# ✅ CORRECT - Links to archived docs
See [old status](docs/archive/reports/SYSTEM_STATUS.md).

# ❌ WRONG - Absolute paths
See /home/pi/zoe/PROJECT_STATUS.md

# ❌ WRONG - Links to moved files
See [status](ZOES_CURRENT_STATE.md)
```

### In Code Comments

```python
# ✅ CORRECT
# See PROJECT_STATUS.md for architecture details

# ❌ WRONG  
# See ZOES_CURRENT_STATE.md for details
```

---

## 📊 Migration Map

### Old → New Mapping

| Old File | New Location | Status |
|----------|--------------|--------|
| `ZOES_CURRENT_STATE.md` | `PROJECT_STATUS.md` | ✅ Replaced |
| `SYSTEM_STATUS.md` | `PROJECT_STATUS.md` | ✅ Replaced |
| `CLEANUP_COMPLETE_SUMMARY.md` | `CLEANUP_SUMMARY.md` | ✅ Renamed |
| `FINAL_STATUS_REPORT.md` | `docs/archive/reports/` | ✅ Archived |
| `ALL_PHASES_COMPLETE.md` | `docs/archive/reports/` | ✅ Archived |
| `AUTHENTICATION-READY.md` | `docs/archive/reports/` | ✅ Archived |
| `*_backup*` files | Deleted | ✅ Removed |
| `routers/archive/` | Deleted | ✅ Removed |
| `ui/archived/` | Deleted | ✅ Removed |

**All references updated**: October 8, 2025 ✅

---

## ✅ Verification Checklist

### For New Code/Scripts

- [ ] References `PROJECT_STATUS.md` (not old status files)
- [ ] References `CLEANUP_SUMMARY.md` (not old cleanup files)
- [ ] Uses relative paths (not absolute)
- [ ] Checks file exists before reading
- [ ] Handles missing files gracefully

### For Documentation

- [ ] Links to current docs (project root)
- [ ] Uses correct filenames
- [ ] Archives old versions (doesn't delete)
- [ ] Updates cross-references
- [ ] Adds "Last Updated" date

### For Maintenance

- [ ] Run `comprehensive_audit.py` monthly
- [ ] Update `PROJECT_STATUS.md` after major changes
- [ ] Archive superseded docs properly
- [ ] Check for broken links
- [ ] Review and clean archive periodically

---

## 🛠️ Maintenance Tools

### Check References
```bash
python3 audit_references.py
```

### Fix References
```bash
python3 fix_references.py
```

### System Audit
```bash
python3 comprehensive_audit.py
```

### Find Broken Links
```bash
grep -r "\.md" *.md docs/ | grep -v "docs/archive" | grep "ZOES\|SYSTEM_STATUS\|CLEANUP_COMPLETE"
```

---

## 📞 Need Help?

### I Can't Find...
1. Check `PROJECT_STATUS.md` first
2. Check `docs/README.md` for index
3. Search archive: `grep -r "keyword" docs/archive/`
4. Run audit: `python3 audit_references.py`

### I Need to Add Documentation
1. Create in project root with clear name
2. Update README or PROJECT_STATUS to reference it
3. Add entry to `docs/README.md`

### I Need to Archive Documentation
1. Move to appropriate `docs/archive/` subdirectory
2. Add date suffix: `DOCUMENT_YYYYMMDD.md`
3. Run `fix_references.py` to update links
4. Document in CHANGELOG

---

## 🎯 Success Criteria

Documentation structure is successful when:

- ✅ All references point to correct files
- ✅ No broken links in core docs
- ✅ Scripts use canonical file paths
- ✅ Automated tools work correctly
- ✅ New developers find docs easily
- ✅ Archive is organized and searchable

---

## 📝 Change Log

| Date | Change | Impact |
|------|--------|--------|
| 2025-10-08 | Consolidated status docs → PROJECT_STATUS.md | All status references updated |
| 2025-10-08 | Created docs/archive/ structure | Old docs organized |
| 2025-10-08 | Renamed CLEANUP_COMPLETE_SUMMARY → CLEANUP_SUMMARY | References updated |
| 2025-10-08 | Removed archive folders (routers, ui) | Clutter eliminated |
| 2025-10-08 | Updated all cross-references | ✅ All references current |

---

**This is the definitive guide for Zoe documentation structure.**  
**All tools, scripts, and processes should follow this structure.**

*Last Updated: October 8, 2025*  
*Next Review: November 8, 2025*

