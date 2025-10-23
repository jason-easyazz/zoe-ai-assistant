# Project Organization - Quick Reference

## Daily Usage

### Before ANY File Changes
```bash
# 1. Validate current state
python3 tools/audit/validate_structure.py

# 2. If deleting a file, check if it's referenced
bash tools/audit/find_file_references.sh <filename>
```

### Before Cleanup Operations
```bash
# 1. Create safety commit
git add -A && git commit -m "Pre-cleanup safety"

# 2. Work in branch
git checkout -b cleanup-$(date +%Y%m%d)

# 3. Run safe cleanup
python3 tools/cleanup/safe_cleanup.py --interactive
```

### Before Committing
The pre-commit hook runs automatically and checks:
- ✅ Project structure validation
- ✅ Critical files exist
- ✅ No junk file patterns

**If hook fails**: Fix the issues, don't bypass with `--no-verify`

## Protection System (7 Layers)

1. **Manifest** (`.zoe/manifest.json`) - File approval registry
2. **Structure Validator** - Checks all files against manifest
3. **Critical Files Validator** - Ensures 42 critical files exist
4. **File Reference Checker** - Prevents deleting referenced files
5. **Pre-Commit Hook** - Automatic validation before commits
6. **Safe Cleanup Tool** - Interactive cleanup assistant
7. **.cursorrules** - Rules for Cursor AI (200+ lines)

## File Categories

### Critical (Never Delete)
- `services/zoe-ui/dist/css/**` - All CSS
- `services/zoe-ui/dist/js/**` - All JavaScript
- `services/zoe-ui/dist/js/widgets/core/**` - All widgets
- `services/zoe-ui/dist/*.html` - All HTML pages
- `services/zoe-core/main.py`, `routers/chat.py` - Backend core

### Prohibited in Root
- `*_backup.*`, `*_old.*`, `*_v2.*` - Use git, not file duplication
- `._*` - Mac metadata files
- `*.log` - Logs don't belong in repo
- `*.jar`, `*.sql`, `*.conf` - Move to proper directories

### Safe to Delete
- `__pycache__/`, `*.pyc` - Python cache
- `.pytest_cache/` - Test cache
- `._*` - Mac metadata

## Folder Organization

```
Root (max 10 .md files)
├── docs/governance/          # Rules & protection
├── tools/audit/              # Validators
├── tools/cleanup/            # Cleanup tools
├── scripts/utilities/        # One-off scripts
├── config/                   # All configs
├── tests/                    # All tests
└── services/                 # Service code
```

## Commands

### Validation
```bash
# Check structure
python3 tools/audit/validate_structure.py

# Check critical files
python3 tools/audit/validate_critical_files.py

# Check file references
bash tools/audit/find_file_references.sh <filename>
```

### Cleanup
```bash
# Dry run (safe - shows what would happen)
python3 tools/cleanup/safe_cleanup.py

# Interactive (asks for each category)
python3 tools/cleanup/safe_cleanup.py --interactive

# Execute (actually deletes)
python3 tools/cleanup/safe_cleanup.py --execute
```

### Testing Pre-Commit Hook
```bash
.git/hooks/pre-commit
```

## Troubleshooting

### "Prohibited files in root"
Move files to proper locations:
- Scripts → `scripts/utilities/`
- Configs → `config/`
- SQL → `data/schema/`

### "Orphan files"
Add to manifest or move to proper category

### "Pre-commit hook failed"
1. Run validators to see what's wrong
2. Fix issues
3. Try commit again

### "Too many .md files in root"
Move docs to `docs/` subdirectories

## Recovery

If you deleted critical files:
```bash
# Find when deleted
git log --all --full-history --oneline -- "path/to/file"

# Restore file
git show <commit>^:path/to/file > path/to/file

# Validate
python3 tools/audit/validate_critical_files.py
```

## Best Practices

1. ✅ **Always validate before cleanup**
2. ✅ **Work in feature branches**
3. ✅ **Create safety commits**
4. ✅ **Test after deletions**
5. ✅ **Let pre-commit hook run**
6. ✅ **Use safe_cleanup.py for bulk operations**
7. ✅ **When in doubt, ASK - don't assume**

## Current Status

- Root: 5/10 .md files ✅
- Critical files: 42/42 present ✅
- Orphan files: 0 ✅
- Prohibited files: 0 ✅
- Pre-commit hook: Active ✅
- Validators: Passing ✅

**System Status**: 🟢 Production Ready

## Quick Fixes

### Clean up Python cache
```bash
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find . -type f -name "*.pyc" -delete
```

### Clean up Mac metadata
```bash
find . -name "._*" -delete
```

### Find large files
```bash
find . -type f -size +10M -not -path "./.git/*"
```

## Documentation

- `docs/governance/CLEANUP_SAFETY.md` - Full safety procedures
- `docs/governance/CRITICAL_FILES.md` - Critical files list
- `docs/governance/MANIFEST_SYSTEM.md` - Manifest system details
- `.zoe/manifest.json` - File approval registry
- `.cursorrules` - Cursor AI rules









