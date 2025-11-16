# Home Directory Rules (/home/pi)

**Critical**: Keep /home/pi clean - it's not a workspace!

---

## 🚨 WHAT BELONGS IN /home/pi

### ✅ ALLOWED (System Files Only)
- `.bash*` - Bash configuration
- `.profile` - Shell profile  
- `.git*` - Git configuration
- `.ssh/` - SSH keys (in .gitignore)
- `.config/` - User config (in .gitignore)
- System dotfiles (`.viminfo`, `.lesshst`, etc.)
- `zoe/` - The project directory

### ❌ FORBIDDEN
- Test scripts (`test_*.py`, `*_test.py`)
- Status reports (`*_STATUS.md`, `*_COMPLETE.md`)
- Feature documentation (`*_GUIDE.md`, `*_DOCUMENTATION.md`)
- Config files (`*.conf`, `*.yml`, `*.yaml`)
- Shell scripts (`*.sh`)
- Python scripts (`fix_*.py`, `create_*.py`, etc.)
- Test results (`*_results.json`)
- Temporary files (`*.tmp`, `*.cache`, `*.bak`)

---

## 📁 WHERE THINGS SHOULD GO

### Test Scripts
```
/home/pi/test_something.py  ❌ WRONG
/home/zoe/assistant/tests/         ✅ RIGHT
```

### Status Reports & Summaries
```
/home/pi/STATUS_REPORT.md           ❌ WRONG
/home/zoe/assistant/docs/archive/reports/  ✅ RIGHT
```

### Config Files
```
/home/pi/nginx.conf        ❌ WRONG
/home/zoe/assistant/config/       ✅ RIGHT
```

### Scripts
```
/home/pi/fix_something.sh             ❌ WRONG
/home/zoe/assistant/scripts/utilities/       ✅ RIGHT
```

### Documentation
```
/home/pi/FEATURE_GUIDE.md          ❌ WRONG
/home/zoe/assistant/docs/guides/          ✅ RIGHT
```

---

## 🔒 ENFORCEMENT

### Pre-Commit Hook
The project pre-commit hook at `/home/zoe/assistant/.git/hooks/pre-commit` will:
- Check project structure (`/home/zoe/assistant`)
- **Does NOT check `/home/pi` directly**

### Manual Check
Run this to check /home/pi cleanliness:
```bash
cd /home/pi
ls -1 *.md *.py *.sh *.json *.conf *.yml 2>/dev/null | wc -l
# Should be: 2-3 files maximum (README.md, CHANGELOG.md from zoe/)
```

### Auto-Cleanup Tool
```bash
cd /home/zoe/assistant
python3 tools/cleanup/clean_home_directory.py
```

This will:
- Move test scripts to `zoe/tests/archive/`
- Move status reports to `zoe/docs/archive/reports/`
- Move configs to `zoe/config/archive/`
- Move scripts to `zoe/scripts/utilities/archive/`
- Delete temp files

---

## 🎯 THE GOLDEN RULE

**If it's related to Zoe, it belongs IN `/home/zoe/assistant`, not `/home/pi`!**

### Quick Decision Tree

```
Created a file in /home/pi?
│
├─ Is it a system dotfile (.bashrc, .profile, etc.)?
│  └─ YES → Leave it in /home/pi
│
├─ Is it the zoe/ directory?
│  └─ YES → Leave it in /home/pi
│
└─ ANYTHING ELSE?
   └─ Move it into /home/zoe/assistant using the cleanup tool
```

---

## 📋 CHECKING COMPLIANCE

### Quick Check
```bash
cd /home/pi
# Count non-hidden files (excluding zoe/)
find . -maxdepth 1 -type f ! -name ".*" | wc -l
# Should be: 0-2 files (README.md, CHANGELOG.md from zoe/ symlinks are OK)
```

### Detailed Check
```bash
cd /home/pi
ls -la | grep -v "^d" | grep -v "^l" | grep -v "^\."
# Should only show system files
```

### Auto-Fix
```bash
cd /home/zoe/assistant
python3 tools/cleanup/clean_home_directory.py
```

---

## 🚨 WHY THIS MATTERS

1. **Clarity**: `/home/pi` is for system config, not project work
2. **Organization**: Everything Zoe-related in one place
3. **Backups**: Easy to backup just `/home/zoe/assistant`
4. **Git**: Clean git status, not cluttered with temp files
5. **Professionalism**: Clean home directory = clean project

---

## 📊 CURRENT STATE

After cleanup on 2025-10-08:
- **Before**: 131 files in /home/pi
- **After**: 13 files in /home/pi (mostly zoe/ subdirectories)
- **Archived**: 118 files moved to `/home/zoe/assistant/` with timestamps

---

## 🔄 MAINTENANCE SCHEDULE

### Weekly
- Run `clean_home_directory.py` to catch strays

### Before Commit
- Check `/home/pi` for orphaned files
- Move any found files to appropriate zoe/ locations

### After Major Work Sessions
- Clean up any test scripts or temp files
- Archive status reports

---

**Remember**: `/home/pi` is NOT a workspace - keep it clean! 🧹

*Last Updated: October 8, 2025*

