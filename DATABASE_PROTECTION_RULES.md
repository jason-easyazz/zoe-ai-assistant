# 🛡️ DATABASE PROTECTION RULES - CRITICAL

**Version**: 1.0  
**Date**: October 26, 2025  
**Status**: 🔒 MANDATORY - NEVER VIOLATE

---

## 🚨 INCIDENT REPORT

**Date**: October 26, 2025  
**Issue**: All user accounts lost (Jason, Andrew, Teneeka, Asya, User)  
**Cause**: Database location confusion and lack of safeguards  
**Resolution**: Fresh slate with strict protection rules

---

## 📍 DATABASE LOCATION - SINGLE SOURCE OF TRUTH

### ✅ CORRECT Location (PRODUCTION)
```
/home/pi/zoe/data/zoe.db
/home/pi/zoe/data/memory.db
/home/pi/zoe/data/training.db
```

### ❌ NEVER Use These Locations
```
/home/pi/zoe-clean/data/          # DELETED - DO NOT RECREATE
/home/pi/zoe_backup/data/         # BACKUP ONLY - READ ONLY
/tmp/                              # TEMPORARY - NEVER PRODUCTION
```

---

## 🔒 MANDATORY RULES

### Rule 1: Database Path Enforcement
**ALL code MUST use environment variable:**
```python
import os
db_path = os.getenv("DATABASE_PATH", "/home/pi/zoe/data/zoe.db")
```

**NEVER hardcode paths:**
```python
# ❌ FORBIDDEN
db_path = "/home/pi/zoe/data/zoe.db"
db_path = "/app/data/zoe.db"
```

### Rule 2: Automatic Backups Before Changes
**Before ANY database modification:**
```bash
# Automatic backup with timestamp
cp /home/pi/zoe/data/zoe.db /home/pi/zoe/data/backups/zoe.db.$(date +%Y%m%d-%H%M%S)
```

### Rule 3: User Data Protection
**NEVER delete users without explicit confirmation:**
```sql
-- ❌ FORBIDDEN without backup
DELETE FROM users;
TRUNCATE users;
DROP TABLE users;
```

### Rule 4: Migration Logging
**ALL schema changes MUST be logged:**
```bash
echo "$(date): Migration XYZ applied" >> /home/pi/zoe/data/migration.log
```

### Rule 5: Database File Permissions
**Production databases are READ-ONLY for non-root:**
```bash
chmod 644 /home/pi/zoe/data/*.db
chown pi:pi /home/pi/zoe/data/*.db
```

---

## 🔧 AUTOMATED SAFEGUARDS

### 1. Pre-Commit Hook (ACTIVE)
Location: `/home/pi/zoe/.git/hooks/pre-commit`

Checks:
- ✅ No hardcoded database paths
- ✅ No database files in commits
- ✅ No deletion of critical tables

### 2. Automatic Backup Script
Location: `/home/pi/zoe/scripts/maintenance/auto_backup.sh`

Schedule: Every 6 hours via cron
```bash
0 */6 * * * /home/pi/zoe/scripts/maintenance/auto_backup.sh
```

### 3. Database Integrity Check
Location: `/home/pi/zoe/scripts/maintenance/check_db_integrity.sh`

Runs: Daily at 2 AM
```bash
0 2 * * * /home/pi/zoe/scripts/maintenance/check_db_integrity.sh
```

### 4. User Count Monitor
Location: `/home/pi/zoe/scripts/maintenance/monitor_users.sh`

Alerts if user count drops below 5

---

## 📋 RECOVERY PROCEDURES

### If Users Are Lost

1. **STOP ALL SERVICES**
   ```bash
   docker stop zoe-core zoe-auth
   ```

2. **Backup Current State**
   ```bash
   cp /home/pi/zoe/data/zoe.db /home/pi/zoe/data/zoe.db.emergency-$(date +%Y%m%d-%H%M%S)
   ```

3. **Restore Users**
   ```bash
   sqlite3 /home/pi/zoe/data/zoe.db < /home/pi/zoe/scripts/setup/create_users.sql
   ```

4. **Verify Users**
   ```bash
   sqlite3 /home/pi/zoe/data/zoe.db "SELECT user_id, username, role FROM users;"
   ```

5. **Restart Services**
   ```bash
   docker start zoe-core zoe-auth
   ```

---

## 🚫 FORBIDDEN ACTIONS

### NEVER Do These Without Explicit User Approval

1. ❌ Delete database files
2. ❌ Truncate user tables
3. ❌ Change database locations
4. ❌ Run migrations without backups
5. ❌ Modify user_id values
6. ❌ Copy databases between directories
7. ❌ Use test databases in production
8. ❌ Overwrite existing databases

---

## ✅ REQUIRED USERS

**Admins (2)**:
- jason (jason@easyazz.com)
- andrew (andrew@easyazz.com)

**Users (3)**:
- teneeka (teneeka@easyazz.com)
- asya (asya@easyazz.com)
- user (user@easyazz.com)

**System (1)**:
- system (system@zoe.local)

**Total: 6 users minimum**

---

## 🔍 VERIFICATION COMMANDS

### Check Database Location
```bash
docker exec zoe-core env | grep DATABASE_PATH
# Should show: DATABASE_PATH=/app/data/zoe.db
```

### Check User Count
```bash
sqlite3 /home/pi/zoe/data/zoe.db "SELECT COUNT(*) FROM users WHERE user_id != 'system';"
# Should show: 5 or more
```

### Check Database Size
```bash
ls -lh /home/pi/zoe/data/zoe.db
# Should be > 1MB
```

### Check Last Backup
```bash
ls -lt /home/pi/zoe/data/backups/ | head -5
# Should show recent backups
```

---

## 📞 EMERGENCY CONTACTS

**If database issues occur:**
1. STOP all services immediately
2. Create emergency backup
3. Contact: jason@easyazz.com
4. DO NOT attempt recovery without approval

---

## 📝 CHANGE LOG

| Date | Change | Reason |
|------|--------|--------|
| 2025-10-26 | Created this document | User data loss incident |
| 2025-10-26 | Implemented auto-backups | Prevent future loss |
| 2025-10-26 | Added user monitoring | Early warning system |

---

**🔒 THIS DOCUMENT IS MANDATORY - VIOLATIONS ARE UNACCEPTABLE**

*Last Updated: October 26, 2025*

