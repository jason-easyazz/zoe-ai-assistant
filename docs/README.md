# Zoe Documentation

## Main Documentation

For current, active documentation, see the project root:

- **README.md** - Project overview and features
- **QUICK-START.md** - How to start and use Zoe
- **CHANGELOG.md** - Version history
- **docs/guides/OPERATOR_RUNBOOK.md** - Current runtime operations
- **HARDWARE_COMPATIBILITY.md** - Platform-specific deployment notes

---

## 📁 This Folder (`/docs/`)

### `/docs/archive/`
Historical documentation organized by category:

**Reports** (`archive/reports/`)
- System status reports
- Phase completion docs
- Integration reports  
- Test results

**Technical** (`archive/technical/`)
- API fixes
- Styling updates
- Debug documentation
- Technical specifications

**Guides** (`archive/guides/`)
- Old integration guides
- Installation docs
- Feature documentation

### `/docs/guides/`
Current user, operator, and developer guides.

### `/docs/governance/`
Repository rules, safety guidance, and design principles.

---

## 🎯 Quick Reference

### I Want To...

**Understand Zoe**: Read `../README.md`
**Start Using Zoe**: Read `../QUICK-START.md`
**Operate Zoe**: Read `guides/OPERATOR_RUNBOOK.md`
**Find Old Reports**: Check `archive/reports/`
**Find Technical Docs**: Check `archive/technical/`
**Troubleshoot**: Run `python3 ../tools/audit/validate_structure.py` and `python3 ../tools/audit/validate_critical_files.py`

---

## 🔍 Finding Documentation

### By Date
All archived docs are organized chronologically within their categories.

### By Type
- **Status**: `archive/reports/`
- **Technical**: `archive/technical/`  
- **Guides**: `archive/guides/`

### By Topic
Use ripgrep to search: `rg "your topic" docs/`

---

## 📝 Documentation Guidelines

### Current vs Archived
- **Current**: Project root (active, maintained)
- **Archived**: `/docs/archive/` (historical reference)

### When to Archive
- When a new status doc supersedes an old one
- When features are deprecated
- When guides are rewritten

### Don't Archive
- Current active documentation
- Frequently referenced guides
- API documentation

---

*For the latest operations guidance, use `docs/guides/OPERATOR_RUNBOOK.md`.*
