# Upgrade Guide: v2.3.0 → v2.3.1

**Release**: Architecture & Performance  
**Date**: October 18, 2025  
**Impact**: Low (mostly backward compatible, one required env var)

---

## 🎯 What's New

This release focuses on **architecture hardening**, **performance optimization**, and **security improvements** based on comprehensive code review feedback.

### Key Improvements
- 🔒 **Security**: CORS restrictions (environment-based)
- ⚡ **Performance**: 10-20x faster database operations
- 🌍 **Portability**: No more hard-coded paths
- 🧠 **Intelligence**: Temporal memory always active
- ✅ **Reliability**: Improved test runner
- 🔧 **Maintainability**: Auto-discovery router system

---

## ⚠️ Required Actions

### 1. Set ALLOWED_ORIGINS Environment Variable

**Why**: CORS is no longer wide open - you must configure allowed origins.

**Docker Compose** (recommended):
```yaml
# docker-compose.yml
services:
  zoe-core:
    environment:
      - ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080,http://localhost:5000
```

**SystemD Service**:
```ini
# /etc/systemd/system/zoe-core.service
[Service]
Environment="ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080,http://localhost:5000"
```

**Shell/Local Dev**:
```bash
export ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080,http://localhost:5000
```

**Production** (use your actual domains):
```bash
ALLOWED_ORIGINS=https://zoe.yourdomain.com,https://app.yourdomain.com
```

---

## 🔄 Upgrade Steps

### Option A: Docker Compose (Recommended)

```bash
# 1. Stop the service
cd /home/pi/zoe
docker-compose down

# 2. Update environment variables
# Edit docker-compose.yml and add:
#   environment:
#     - ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080,http://localhost:5000

# 3. Pull latest changes (if using git)
git pull origin main

# 4. Rebuild and restart
docker-compose build zoe-core
docker-compose up -d

# 5. Verify
docker-compose logs -f zoe-core | grep "RouterLoader\|CORS\|Temporal"
# Look for:
#   ✅ Temporal memory integration initialized (REQUIRED)
#   📦 Discovered XX routers
#   CORS middleware loaded with restricted origins
```

### Option B: SystemD Service

```bash
# 1. Stop the service
sudo systemctl stop zoe-core

# 2. Update environment variables
sudo nano /etc/systemd/system/zoe-core.service
# Add under [Service]:
#   Environment="ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080"

# 3. Reload systemd
sudo systemctl daemon-reload

# 4. Pull latest changes
cd /home/pi/zoe
git pull origin main

# 5. Restart service
sudo systemctl start zoe-core

# 6. Verify
sudo journalctl -u zoe-core -f | grep "RouterLoader\|CORS\|Temporal"
```

### Option C: Manual/Development

```bash
# 1. Set environment variable
export ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080,http://localhost:5000

# 2. Pull latest changes
cd /home/pi/zoe
git pull origin main

# 3. Navigate to service directory
cd services/zoe-core

# 4. Restart service
# If using screen/tmux:
# Ctrl+C to stop, then:
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 5. Verify in logs
# Look for:
#   ✅ Temporal memory integration initialized (REQUIRED)
#   📦 Discovered XX routers
```

---

## ✅ Verification Checklist

After upgrading, run these checks:

### 1. Run Verification Script
```bash
cd /home/pi/zoe
./tools/validation/verify_cursor_fixes.sh
```
**Expected**: `22/22 checks passing`

### 2. Check Service Logs
```bash
# Docker
docker-compose logs zoe-core | tail -50

# SystemD
sudo journalctl -u zoe-core -n 50

# Manual
# Check your terminal output
```

**Look for these log messages**:
- ✅ `Temporal memory integration initialized (REQUIRED)`
- ✅ `📦 Discovered XX routers`
- ✅ `✅ Registered router: auth`
- ✅ `✅ Registered router: chat`
- ❌ NO `allow_origins=["*"]` warnings

### 3. Test API Endpoints
```bash
# Health check
curl http://localhost:8000/health

# Chat endpoint
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "user_id": "test"}'
```

### 4. Test CORS (should work from allowed origins only)
```bash
# From allowed origin (should work)
curl -H "Origin: http://localhost:3000" http://localhost:8000/health

# From disallowed origin (should be blocked)
curl -H "Origin: http://evil.com" http://localhost:8000/health
```

### 5. Run Test Suite
```bash
cd /home/pi/zoe
./tests/run_all_tests.sh
```

---

## 🐛 Troubleshooting

### Issue: "CORS error" in browser console

**Cause**: Your frontend origin is not in ALLOWED_ORIGINS

**Fix**:
```bash
# Add your frontend URL to ALLOWED_ORIGINS
ALLOWED_ORIGINS=http://localhost:3000,http://your-frontend-url:port
```

### Issue: "Module not found: temporal_memory_integration"

**Cause**: Temporal memory module missing (should not happen, but just in case)

**Fix**:
```bash
# Check if file exists
ls -l /home/pi/zoe/services/zoe-core/temporal_memory_integration.py

# If missing, reinstall:
cd /home/pi/zoe
git checkout services/zoe-core/temporal_memory_integration.py
```

### Issue: "Database locked" errors

**Cause**: Old connections not closed properly

**Fix**:
```bash
# Restart the service completely
# Docker:
docker-compose restart zoe-core

# SystemD:
sudo systemctl restart zoe-core

# Manual: Ctrl+C and restart uvicorn
```

### Issue: Service won't start after upgrade

**Check**:
1. **Syntax errors**: `python3 -m py_compile /home/pi/zoe/services/zoe-core/main.py`
2. **Import errors**: `cd /home/pi/zoe/services/zoe-core && python3 -c "from router_loader import RouterLoader"`
3. **Logs**: Check service logs for specific error messages

---

## 📊 Performance Monitoring

After upgrading, monitor these metrics:

### Database Performance
```bash
# Watch database file size
watch -n 5 'ls -lh /app/data/memory.db'

# Check for WAL mode (should see .wal file)
ls -lh /app/data/memory.db*
# Expected: memory.db, memory.db-wal, memory.db-shm
```

### Response Times
```bash
# Test chat endpoint response time
time curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "user_id": "test"}'

# Should be <500ms (was: 1-2s)
```

### Router Registration
```bash
# Check how many routers were auto-discovered
docker-compose logs zoe-core 2>&1 | grep "Discovered.*routers"
# Expected: 30-40 routers
```

---

## 🔙 Rollback Procedure

If you need to rollback:

```bash
# 1. Stop service
docker-compose down  # or: sudo systemctl stop zoe-core

# 2. Checkout previous version
cd /home/pi/zoe
git checkout v2.3.0

# 3. Restore old CORS (if needed)
# Edit services/zoe-core/main.py:
#   allow_origins=["*"]

# 4. Restart
docker-compose up -d  # or: sudo systemctl start zoe-core
```

---

## 📚 Additional Resources

- **Full changelog**: `/home/pi/zoe/CHANGELOG.md`
- **Detailed fixes report**: `/home/pi/zoe/docs/CURSOR_FEEDBACK_FIXES.md`
- **Quick reference**: `/home/pi/zoe/FIXES_QUICK_REFERENCE.md`
- **Environment variables guide**: `/home/pi/zoe/docs/ENVIRONMENT_VARIABLES.md`

---

## 💬 Support

If you encounter issues:

1. **Check logs** - Most issues are logged clearly
2. **Run verification script** - `./tools/validation/verify_cursor_fixes.sh`
3. **Check documentation** - See resources above
4. **File an issue** - Include logs and error messages

---

## ✨ What You'll Notice

After upgrading:

- ✅ **Faster responses** - Especially on database-heavy operations
- ✅ **Better conversation continuity** - Temporal memory always active
- ✅ **Cleaner logs** - Router registration is logged clearly
- ✅ **More secure** - CORS properly restricted
- ✅ **More reliable tests** - Test runner fails fast

**Happy upgrading!** 🚀

