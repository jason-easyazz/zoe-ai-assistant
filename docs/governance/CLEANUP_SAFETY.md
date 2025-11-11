# 🛡️ CLEANUP SAFETY RULES - MANDATORY FOR ALL AI ASSISTANTS

**CRITICAL:** These rules MUST be followed before deleting ANY files in the Zoe project.

**Last Incident:** October 19, 2025 - "Ultra-aggressive cleanup" deleted critical CSS/JS files, breaking entire frontend

---

## 🚨 NEVER DELETE WITHOUT THESE STEPS

### STEP 1: Run Critical Files Validator
```bash
python3 /home/zoe/assistant/tools/audit/validate_critical_files.py
```
**If this fails, STOP. Do not delete anything.**

### STEP 2: Check File References
```bash
bash /home/zoe/assistant/tools/audit/find_file_references.sh <filename>
```
**If references found, STOP. File is in use.**

### STEP 3: Create Safety Commit
```bash
git add -A
git commit -m "Pre-cleanup safety commit - $(date)"
```
**If unsure, commit. Git is free, broken systems are expensive.**

### STEP 4: Work in Feature Branch
```bash
git checkout -b cleanup-$(date +%Y%m%d)
```
**NEVER cleanup directly on main/master.**

### STEP 5: Delete Incrementally
- Delete 5-10 files at a time
- Test after EACH deletion
- Commit working states
- Check browser console (F12) for errors

### STEP 6: Test Thoroughly
```bash
# For frontend changes:
# 1. Hard refresh browser (Ctrl+Shift+R)
# 2. Load ALL major pages (dashboard, chat, lists, calendar, journal, memories, settings)
# 3. Check browser console for errors
# 4. Verify widgets load on dashboard
# 5. Test login/auth

# For backend changes:
docker-compose ps
docker-compose logs zoe-core --tail 50
curl http://localhost/api/health
```

---

## ❌ ABSOLUTE PROHIBITIONS

### NEVER Delete These File Types:
- Any `.css` file in `services/zoe-ui/dist/css/`
- Any `.js` file in `services/zoe-ui/dist/js/`
- Any widget file in `services/zoe-ui/dist/js/widgets/`
- Any component in `services/zoe-ui/dist/components/`
- Any HTML page in `services/zoe-ui/dist/`
- `main.py`, `ai_client.py`, or router files in backend
- `docker-compose.yml`, `.env`, or nginx configs

### NEVER Use These Phrases in Cleanup:
- ❌ "Ultra-aggressive cleanup"
- ❌ "Remove all unused files"
- ❌ "Clean everything"
- ❌ "Delete large directories"
- ❌ "Aggressive optimization"

### ALWAYS Ask Before Deleting:
- Any file over 100KB
- Any directory with more than 10 files
- Any file that's been in the repo > 30 days
- Anything in `services/` directories

---

## ✅ SAFE DELETION TARGETS (Examples)

### Safe to Delete:
- `*.pyc`, `__pycache__/`, `.pytest_cache/`
- `*.tmp`, `*.cache`, `*.log` in `/tmp` or temp dirs
- Files explicitly marked as `*_backup`, `*_old` **AFTER VERIFICATION**
- Documentation in `docs/archive/` dated > 6 months old
- `node_modules/` (can reinstall)

### Requires Verification:
- Any `*_backup.*`, `*_old.*`, `*_v2.*` files
- Test files outside `tests/` directory
- Scripts not in `scripts/` directory
- Documentation outside `docs/`

### Requires Approval:
- Any file in `services/` directories
- Any config file (`.yml`, `.conf`, `.json`)
- Any file referenced in git history within 30 days

---

## 🔧 RECOVERY PROCEDURES

If critical files were deleted:

### 1. Find When Deleted
```bash
git log --diff-filter=D --summary | grep <filename>
```

### 2. Restore from Git
```bash
# Find the commit before deletion
git log --all --full-history --oneline -- "path/to/file"

# Restore the file
git show <commit-hash>^:path/to/file > path/to/file
```

### 3. Restore Multiple Files
```bash
# Restore entire directory
git checkout <commit-hash>^ -- services/zoe-ui/dist/css/
git checkout <commit-hash>^ -- services/zoe-ui/dist/js/
```

### 4. Verify Restoration
```bash
python3 /home/zoe/assistant/tools/audit/validate_critical_files.py
docker restart zoe-ui
# Test in browser
```

---

## 📋 PRE-CLEANUP CHECKLIST

Before EVERY cleanup operation, verify:

- [ ] Ran `validate_critical_files.py` - passed
- [ ] Ran `find_file_references.sh` for each file - no references
- [ ] Created safety commit with timestamp
- [ ] Working in feature branch, not main
- [ ] Know how to restore if needed (tested git show)
- [ ] Deleting < 10 files at a time
- [ ] Have tested file deletion in isolation
- [ ] Browser console shows no new errors
- [ ] All major pages load correctly
- [ ] Docker containers still running
- [ ] Created documentation of what was deleted and why

---

## 🎯 DECISION TREE

```
Should I delete this file?
│
├─ Is it in CRITICAL_FILES_DO_NOT_DELETE.md?
│  └─ YES → ❌ STOP. Do not delete.
│
├─ Is it referenced by find_file_references.sh?
│  └─ YES → ❌ STOP. File is in use.
│
├─ Is it in services/zoe-ui/dist/? 
│  └─ YES → ⚠️  Verify with validator, then check references.
│
├─ Is it in services/zoe-core/ or services/zoe-auth/?
│  └─ YES → ❌ Backend files require explicit approval.
│
├─ Is it a config file (.yml, .conf, .json)?
│  └─ YES → ❌ Config files require explicit approval.
│
├─ Is it *.pyc, __pycache__, or *.tmp?
│  └─ YES → ✅ Safe to delete (but check it's truly temp).
│
├─ Is it marked *_backup or *_old?
│  └─ Check git: was it committed recently?
│     ├─ YES → ⚠️  Ask why it exists.
│     └─ NO → ✅ Probably safe, but verify no references.
│
└─ When in doubt?
   └─ ❌ DON'T DELETE. Move to docs/archive/ instead.
```

---

## 💡 BEST PRACTICES

### Instead of Deleting:
1. **Archive** - Move to `docs/archive/category/filename_YYYYMMDD.ext`
2. **Comment** - Add `# DEPRECATED: reason` at top of file
3. **Rename** - Add `.deprecated` suffix instead of deleting
4. **Document** - Note in CHANGELOG.md why it's no longer needed

### Cleanup Philosophy:
- **Git is your backup** - Use it, don't fear it
- **Test often, commit often** - Small changes, big safety
- **Move, don't delete** - Reversible is better than perfect
- **Document decisions** - Future you will thank present you

### Red Flags:
- Deleting files you didn't create
- Deleting files you don't understand
- Deleting files "to save space" (git handles this)
- Deleting files because they "look unused"
- Batch deleting without testing each file

---

## 📞 INCIDENT RESPONSE

If cleanup broke something:

### Immediate Actions:
1. **STOP** all cleanup operations
2. **DOCUMENT** what was deleted
3. **RUN** recovery procedures above
4. **TEST** thoroughly after recovery
5. **REPORT** what happened and why

### Post-Incident:
1. Update `CRITICAL_FILES_DO_NOT_DELETE.md` with new critical files
2. Add file pattern to `validate_critical_files.py`
3. Document lessons learned
4. Review cleanup process for gaps

---

## 🔗 RELATED DOCUMENTATION

- `/home/zoe/assistant/docs/CRITICAL_FILES_DO_NOT_DELETE.md` - Comprehensive list
- `/home/zoe/assistant/tools/audit/validate_critical_files.py` - Validation tool
- `/home/zoe/assistant/tools/audit/find_file_references.sh` - Reference checker
- `/home/zoe/assistant/PROJECT_STRUCTURE_RULES.md` - File organization rules

---

## 🎓 LESSONS LEARNED (October 2025 Incident)

**What Happened:**
- "Ultra-aggressive cleanup" deleted CSS and JavaScript files
- All frontend pages broke with blue backgrounds
- Widget system failed with "Unexpected token '<'" errors
- Journal had CORS errors due to hardcoded localhost

**Root Causes:**
1. No validation before deletion
2. Deleted files referenced by HTML
3. No incremental testing
4. "Aggressive" approach instead of cautious
5. No safety commit before operation

**Prevention (Now Implemented):**
1. ✅ Critical files validator (`validate_critical_files.py`)
2. ✅ Reference checker (`find_file_references.sh`)
3. ✅ Comprehensive documentation (`CRITICAL_FILES_DO_NOT_DELETE.md`)
4. ✅ Mandatory checklist (this document)
5. ✅ Recovery procedures documented

**Recovery Time:** 2 hours
**Files Restored:** 27 critical files
**Services Affected:** All frontend pages, docker-compose

---

## ⚖️ THE GOLDEN RULE

> **"When in doubt, DON'T delete. Moving is free, breaking is expensive."**

Every deleted file should be:
1. Validated as safe
2. Checked for references  
3. Tested in isolation
4. Documented in commit
5. Reversible via git

If you can't check all 5, **DON'T DELETE**.

---

**Last Updated:** October 19, 2025
**Mandatory for:** All AI assistants (Claude, Cursor, etc.)
**Enforced by:** Pre-cleanup validation scripts

