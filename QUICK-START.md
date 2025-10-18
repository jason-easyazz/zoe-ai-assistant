# 🌟 Zoe AI Assistant - Quick Start

## 🆕 First-Time Setup (New Installations)

If this is your first time installing Zoe, initialize the databases:

```bash
cd /home/pi/zoe

# Initialize databases from schemas
./scripts/setup/init_databases.sh

# Optional: Add demo data for testing
./scripts/setup/init_databases.sh --with-seed-data
```

**Note**: Existing installations don't need this step - your databases already exist!

## 🚀 How to Start Zoe

```bash
cd /home/pi/zoe
./start-zoe.sh
```

## 📱 How to Use

1. **Open browser**: http://localhost:8090
2. **Touch the orb** - it will show user profiles
3. **Select a profile** - profiles will arrange along the top
4. **Authenticate**:
   - **PIN**: Use the number pad (default: admin/admin, user/user)  
   - **Password**: Click "Password" tab for touch keyboard
   - **Guest**: Click the guest profile for instant access

## 🔑 Default Credentials

- **Admin**: username=`admin`, password/pin=`admin`
- **User**: username=`user`, password/pin=`user`
- **Guest**: No credentials needed

## 🛑 How to Stop Zoe

```bash
./stop-zoe.sh
```

## ✅ What's Working

- ✅ Beautiful animated orb
- ✅ Profile selection with smooth animations
- ✅ PIN pad authentication
- ✅ Touch keyboard for passwords
- ✅ Guest access
- ✅ Session management
- ✅ Navigation between pages
- ✅ Logout functionality
- ✅ Responsive design for touch screens

## 🐛 If Something's Wrong

1. **Check if services are running**:
   ```bash
   curl http://localhost:8090
   curl http://localhost:8002/health
   ```

2. **Check logs**:
   ```bash
   tail -f /tmp/zoe-auth.log
   tail -f /tmp/zoe-ui.log
   ```

3. **Restart everything**:
   ```bash
   ./stop-zoe.sh
   ./start-zoe.sh
   ```

## 📁 File Structure

- `/home/pi/zoe/start-zoe.sh` - Start everything
- `/home/pi/zoe/stop-zoe.sh` - Stop everything  
- `/home/pi/zoe/services/zoe-auth/` - Authentication service
- `/home/pi/zoe/services/zoe-ui/dist/` - Web interface
- `/tmp/zoe-*.log` - Log files

## 🔄 For Developers

### Conventional Commits (Required)

All commits must follow the format: `type(scope): description`

```bash
git commit -m "feat(chat): Add voice command support"
git commit -m "fix(calendar): Fix timezone handling"
git commit -m "docs: Update API documentation"
```

**See**: `docs/guides/CHANGE_MANAGEMENT.md` for full details

### Database Changes

After modifying database schema:

```bash
# Export updated schema
./scripts/maintenance/export_schema.sh

# Commit schema files
git add data/schema/*.sql
git commit -m "db: Add user_preferences table"
```

### Change Tracking

```bash
# See this week's changes
./tools/reports/weekly_summary.sh

# Check repository health
python3 tools/reports/repo_health.py

# Generate CHANGELOG for release
python3 tools/generators/generate_changelog.py --version v2.4.0
```

---
**The authentication system is now fully working with no mixed content issues!** 🎉
