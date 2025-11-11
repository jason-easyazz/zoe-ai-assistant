# 🚨 CRITICAL FILES - DO NOT DELETE

**Last Updated:** October 19, 2025
**Purpose:** Prevent accidental deletion of essential files during cleanup operations

## ⚠️ ULTRA-CRITICAL RULE

**NEVER run cleanup operations without:**
1. Running the validation script first: `python3 tools/audit/validate_critical_files.py`
2. Creating a git commit with current state
3. Testing in a branch, not main/master
4. Verifying file references with: `tools/audit/find_file_references.sh`

---

## 📂 FRONTEND CRITICAL FILES

### Core CSS (NEVER DELETE)
```
services/zoe-ui/dist/css/
├── glass.css                    # Main glassmorphism theme - CRITICAL
└── memories-enhanced.css        # Enhanced memory styling - CRITICAL
```

**Impact if deleted:** Entire UI loses styling, pages appear broken with blue backgrounds

---

### Core JavaScript (NEVER DELETE)
```
services/zoe-ui/dist/js/
├── auth.js                      # Authentication system - CRITICAL
├── common.js                    # Shared API functions - CRITICAL
├── widget-system.js             # Dashboard widget framework - CRITICAL
├── widget-base.js               # Widget base class - CRITICAL
├── ai-processor.js              # AI integration - IMPORTANT
├── zoe-orb.js                   # Zoe orb interface - IMPORTANT
├── journal-api.js               # Journal functionality - IMPORTANT
├── journal-ui-enhancements.js   # Journal UI - IMPORTANT
├── memory-graph.js              # Memory visualization - IMPORTANT
├── memory-search.js             # Memory search - IMPORTANT
├── memory-timeline.js           # Memory timeline - IMPORTANT
├── settings.js                  # Settings page - IMPORTANT
└── wikilink-parser.js           # Wikilink parsing - IMPORTANT
```

**Impact if deleted:** Pages break, JavaScript errors, features stop working

---

### Widget Modules (NEVER DELETE)
```
services/zoe-ui/dist/js/widgets/core/
├── events.js                    # Calendar widget - CRITICAL
├── tasks.js                     # Tasks widget - CRITICAL
├── time.js                      # Time widget - CRITICAL
├── weather.js                   # Weather widget - CRITICAL
├── home.js                      # Smart home widget - CRITICAL
├── system.js                    # System status widget - CRITICAL
├── notes.js                     # Notes widget - CRITICAL
└── zoe-orb.js                   # Orb widget - CRITICAL
```

**Impact if deleted:** Dashboard shows "Unexpected token '<'" errors, widgets fail to load

---

### Components (NEVER DELETE)
```
services/zoe-ui/dist/components/
├── zoe-orb.html                 # Orb component - CRITICAL
└── zoe-orb-complete.html        # Complete orb - CRITICAL
```

**Impact if deleted:** Orb fails to load on all pages

---

### Touch Interface (NEVER DELETE IF USING TOUCH PANEL)
```
services/zoe-ui/dist/touch/
├── css/
│   ├── ambient.css              # Touch styling
│   ├── gestures.css             # Gesture support
│   └── touch-base.css           # Base touch styles
└── js/
    ├── ambient-widgets.js       # Ambient mode
    ├── biometric-auth.js        # Biometric auth
    ├── gestures.js              # Touch gestures
    ├── photo-slideshow.js       # Photo features
    ├── presence-detection.js    # Presence detection
    ├── touch-common.js          # Touch utilities
    └── voice-touch.js           # Voice integration
```

**Impact if deleted:** Touch panel stops working

---

### HTML Pages (NEVER DELETE)
```
services/zoe-ui/dist/
├── index.html                   # Landing page - CRITICAL
├── auth.html                    # Authentication - CRITICAL
├── chat.html                    # Main chat interface - CRITICAL
├── dashboard.html               # Dashboard - CRITICAL
├── calendar.html                # Calendar - CRITICAL
├── lists.html                   # Lists/todos - CRITICAL
├── journal.html                 # Journal - CRITICAL
├── memories.html                # Memories - CRITICAL
└── settings.html                # Settings - CRITICAL
```

**Impact if deleted:** Page returns 404, feature unavailable

---

## 🔧 BACKEND CRITICAL FILES

### Core Services (NEVER DELETE)
```
services/zoe-core/
├── main.py                      # Main FastAPI app - CRITICAL
├── routers/
│   └── chat.py                  # SINGLE chat router - CRITICAL
├── ai_client.py                 # AI integration - CRITICAL
├── requirements.txt             # Dependencies - CRITICAL
└── Dockerfile                   # Container config - CRITICAL
```

**Impact if deleted:** Backend stops working, API endpoints fail

---

### Authentication (NEVER DELETE)
```
services/zoe-auth/
├── main.py                      # Auth service - CRITICAL
├── auth.py                      # Auth logic - CRITICAL
└── Dockerfile                   # Container config - CRITICAL
```

**Impact if deleted:** Cannot login, authentication fails

---

### Configuration (NEVER DELETE)
```
/home/zoe/assistant/
├── docker-compose.yml           # Service orchestration - CRITICAL
├── .env                         # Environment variables - CRITICAL
└── nginx.conf                   # Web server config - CRITICAL
```

**Impact if deleted:** Services won't start, app unusable

---

## 🚫 SAFE TO DELETE (EXAMPLES)

### Temporary Files
- `*.tmp`, `*.cache`, `*.log` (in /tmp or marked as temp)
- `__pycache__/`, `*.pyc`, `.pytest_cache/`
- `node_modules/` (can be reinstalled)

### Backup Files (with date stamps)
- `docs/archive/reports/STATUS_REPORT_YYYYMMDD.md`
- `backups/local/*` (if backups confirmed elsewhere)

### Old Documentation (after archiving)
- Superseded guides in `docs/archive/`
- Old technical docs moved to archive

### Development/Test Files (if not in use)
- `test_*.py` in wrong locations (move, don't delete)
- Experimental scripts that are documented as unused

---

## ✅ CLEANUP BEST PRACTICES

### Before ANY Cleanup Operation:

1. **Check Git Status**
   ```bash
   git status
   git diff
   ```

2. **Create Safety Commit**
   ```bash
   git add -A
   git commit -m "Pre-cleanup safety commit"
   ```

3. **Run Validation**
   ```bash
   python3 tools/audit/validate_critical_files.py
   ```

4. **Check File References**
   ```bash
   bash tools/audit/find_file_references.sh <filename>
   ```

5. **Create Branch**
   ```bash
   git checkout -b cleanup-YYYYMMDD
   ```

6. **Small Increments**
   - Delete 5-10 files at a time
   - Test after each deletion
   - Commit working state

7. **Test Thoroughly**
   - Load every major page
   - Check browser console (F12)
   - Verify no errors

8. **Document Changes**
   ```bash
   git commit -m "cleanup: Removed X deprecated files in category Y
   
   - File1: reason
   - File2: reason
   Verified no references, tested all pages"
   ```

---

## 🔍 VALIDATION RULES

### CSS Files
- ✅ Keep: Any `.css` file in `dist/css/`
- ❌ Delete: `.css` files marked as `.backup` or `.old`

### JavaScript Files  
- ✅ Keep: Any `.js` in `dist/js/` or `dist/js/widgets/`
- ❌ Delete: Duplicate files with `_backup`, `_old`, `_v2` suffixes

### HTML Files
- ✅ Keep: All primary page HTML files in `dist/`
- ⚠️ Careful: Developer pages (check usage first)
- ❌ Delete: Test/demo HTML files clearly marked as such

### Component Files
- ✅ Keep: Anything in `components/` directory
- ❌ Never delete without checking HTML references

---

## 📊 IMPACT LEVELS

| Level | Description | Examples |
|-------|-------------|----------|
| 🔴 **CRITICAL** | Deleting breaks core functionality | glass.css, auth.js, main.py |
| 🟠 **IMPORTANT** | Deleting breaks specific features | widget files, journal-api.js |
| 🟡 **MODERATE** | Deleting causes degraded UX | touch interface, developer tools |
| 🟢 **LOW** | Safe to remove with caution | backup files, old documentation |
| ⚪ **SAFE** | Always safe to remove | .pyc, __pycache__, *.tmp |

---

## 🆘 RECOVERY PROCEDURES

### If Critical Files Deleted:

1. **Don't Panic - Git Has Backups**
   ```bash
   git log --all --full-history --oneline -- "path/to/file"
   ```

2. **Restore Single File**
   ```bash
   git show COMMIT_HASH:path/to/file > path/to/file
   ```

3. **Restore Multiple Files**
   ```bash
   git checkout COMMIT_HASH^ -- services/zoe-ui/dist/css/
   git checkout COMMIT_HASH^ -- services/zoe-ui/dist/js/
   ```

4. **Find When File Was Deleted**
   ```bash
   git log --diff-filter=D --summary | grep filename
   ```

5. **Verify Restoration**
   ```bash
   ls -la path/to/restored/files
   git diff
   ```

6. **Test Immediately**
   - Hard refresh browser (Ctrl+Shift+R)
   - Check console for errors
   - Test affected features

---

## 📝 CLEANUP CHECKLIST

Before approving ANY cleanup PR or operation:

- [ ] Validation script passed
- [ ] Git safety commit created
- [ ] Working in feature branch, not main
- [ ] All HTML references checked
- [ ] Browser console tested (no new errors)
- [ ] All pages load correctly
- [ ] Deleted files documented in commit message
- [ ] Team notified if removing shared resources
- [ ] Backup verified if removing backups

---

## 🎯 GOLDEN RULES

1. **If unsure, DON'T delete** - Move to `docs/archive/` instead
2. **Test BEFORE committing** - One broken deployment is worse than clutter
3. **Small commits** - Easy to revert, easy to review
4. **Document reasoning** - Future you will thank present you
5. **Use git history** - Don't duplicate files, use version control
6. **Validate after restore** - Restoration doesn't guarantee functionality

---

## 📞 EMERGENCY CONTACTS

**If you break something during cleanup:**

1. Stop immediately
2. Document what was deleted
3. Run recovery procedures above
4. Check this document for restoration steps
5. Test thoroughly before continuing

**Prevention > Recovery**

