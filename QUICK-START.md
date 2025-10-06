# 🌟 Zoe AI Assistant - Quick Start

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

---
**The authentication system is now fully working with no mixed content issues!** 🎉
