# Manifest System Documentation

## Overview

The Zoe Project uses a manifest-based file approval system to prevent accidental deletion of critical files and maintain clean project structure.

## How It Works

### Manifest Location
`.zoe/manifest.json` - Central registry of all approved files and patterns

### File Categories

**1. Critical Files**
- Never delete without extreme caution
- Includes UI core (CSS, JS), backend core, config files
- Deletion requires manual intervention

**2. Approved Patterns**
- Glob patterns for standard project files
- Examples: `docs/**/*.md`, `tests/**/*.py`
- Files matching these patterns are automatically approved

**3. Safe to Delete**
- Files that can be auto-deleted without concern
- Examples: `__pycache__`, `*.pyc`, `._*` (Mac metadata)

**4. Prohibited in Root**
- File patterns that should never be in project root
- Examples: `*.jar`, `*.log`, `*_backup.*`

**5. Orphan Files**
- Files not matching any category
- Requires manual review before deletion

## Tools

### Structure Validator
```bash
python3 tools/audit/validate_structure.py
```

Validates all files against manifest:
- Lists prohibited files in root
- Identifies orphan files
- Checks root .md file limit (max 10)
- Exit code 0 = pass, 1 = fail

### Critical Files Validator
```bash
python3 tools/audit/validate_critical_files.py
```

Ensures all critical files exist:
- Checks 40+ critical files
- Detects dangerous patterns (*_backup, *_old)
- Prevents commits if critical files missing

### File Reference Checker
```bash
bash tools/audit/find_file_references.sh <filename>
```

Searches for file references:
- Checks HTML, JS, Python, config files
- Shows where file is used
- Blocks deletion if file is referenced

### Safe Cleanup Tool
```bash
python3 tools/cleanup/safe_cleanup.py
```

Interactive cleanup assistant:
- Scans for orphan files
- Categorizes each file
- Interactive mode for user decisions
- Dry-run by default

## Pre-Commit Hook

Located at `.git/hooks/pre-commit`, automatically runs before every commit:

1. Validates project structure
2. Validates critical files exist
3. Blocks junk file patterns (._*, *_backup, *_old)

Commit is BLOCKED if any validation fails.

## Adding New Files

When adding new critical files or patterns:

1. Edit `.zoe/manifest.json`
2. Add file path to appropriate category
3. Run validator to confirm
4. Commit manifest changes with the new files

## Workflow

### Before Cleanup
```bash
# 1. Validate current state
python3 tools/audit/validate_structure.py

# 2. Create safety commit
git add -A && git commit -m "Pre-cleanup safety"

# 3. Work in branch
git checkout -b cleanup-$(date +%Y%m%d)

# 4. Run safe cleanup
python3 tools/cleanup/safe_cleanup.py --interactive
```

### Before Deleting a File
```bash
# 1. Check if file is critical
python3 tools/audit/validate_critical_files.py

# 2. Check if file is referenced
bash tools/audit/find_file_references.sh <filename>

# 3. If both pass, delete and test
rm <filename>

# 4. Validate after deletion
python3 tools/audit/validate_structure.py
```

## File Limits

- **Root .md files**: Max 10
- **Prohibited patterns in root**: None allowed
- **Critical files**: All must exist

## Recovery

If critical files are accidentally deleted:

```bash
# Find when file was deleted
git log --all --full-history --oneline -- "path/to/file"

# Restore file
git show <commit>^:path/to/file > path/to/file

# Validate restoration
python3 tools/audit/validate_critical_files.py
```

## Best Practices

1. **Always validate before cleanup**
2. **Work in feature branches, not main**
3. **Create safety commits before deletions**
4. **Test after each deletion**
5. **Update manifest when adding new critical files**
6. **Never bypass pre-commit hooks**
7. **Ask user for orphan files - don't assume**

## Maintenance

### Update Manifest
Edit `.zoe/manifest.json` when:
- Adding new critical files
- Changing project structure
- Adding new approved patterns
- Updating prohibited patterns

### Test Validators
```bash
# Run all validators
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py

# Should both pass before any commit
```

## See Also

- `docs/governance/CLEANUP_SAFETY.md` - Safety procedures
- `docs/governance/CRITICAL_FILES.md` - Critical files list
- `PROJECT_STRUCTURE_RULES.md` - Project organization rules









