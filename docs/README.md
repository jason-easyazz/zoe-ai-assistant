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

Retired documents are removed from the working tree. Git history keeps the old
bytes; do not recreate `docs/archive/`.

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
**Find Old Reports**: Use `git log -- docs/`
**Find Technical Docs**: Search active categories or git history
**Troubleshoot**: Run `python3 ../tools/audit/validate_structure.py` and `python3 ../tools/audit/validate_critical_files.py`

---

## 🔍 Finding Documentation

### By Type
- **Status/reviews**: `reviews/` or `post-mortems/`
- **Technical**: `developer/` or `architecture/`
- **Guides**: `guides/`

### By Topic
Use ripgrep to search: `rg "your topic" docs/`

---

## 📝 Documentation Guidelines

### Current vs Retired
- **Current**: Project root (active, maintained)
- **Retired**: removed from the working tree; recover from git history

### When to Retire
- When a new status doc supersedes an old one
- When features are deprecated
- When guides are rewritten

### Don't Retire
- Current active documentation
- Frequently referenced guides
- API documentation

---

*For the latest operations guidance, use `docs/guides/OPERATOR_RUNBOOK.md`.*
