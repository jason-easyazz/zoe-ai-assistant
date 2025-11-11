# Zoe PWA - Quick Start Card

## 🚀 Deploy in 3 Steps (2 minutes)

```bash
# 1. Database
sqlite3 /home/zoe/assistant/data/zoe.db < /home/zoe/assistant/services/zoe-core/db/schema/push_subscriptions.sql

# 2. Dependencies
docker exec zoe-core pip install py-vapid==1.9.1 pywebpush==2.0.0

# 3. Restart
docker restart zoe-core && sleep 10

# 4. Test
curl http://localhost:8000/api/push/vapid-public-key | jq .publicKey
```

**Success if you see a long base64 key!**

---

## 📱 Install on Phone

### Android:
Visit Zoe URL → Tap "Install" banner → Grant permission → Done!

### iOS:
Visit in Safari → Share → "Add to Home Screen" → Grant permission → Done!

---

## 🔔 Test Notifications

In browser console:
```javascript
await zoePushNotifications.sendTest()
```

Should receive notification on your device!

---

## ⚙️ Configure

Settings → Notification Settings → Customize preferences

---

## 🎯 Features

✅ Installable app  
✅ Gradient orb icon  
✅ Calendar reminders (15 min before)  
✅ Task due alerts  
✅ Works when app closed  
✅ iOS Shortcuts support  
✅ Offline mode  
✅ Multi-device sync  

---

## 📞 Quick Commands

```bash
# Test API
curl http://localhost:8000/api/push/vapid-public-key

# Check services
docker logs zoe-core --tail 20 | grep reminder

# View subscriptions
sqlite3 /home/zoe/assistant/data/zoe.db "SELECT * FROM push_subscriptions;"

# Restart
docker restart zoe-core
```

---

## 🔥 One-Line Deploy

```bash
cd /home/zoe/assistant && ./scripts/deployment/deploy-phase2-push-notifications.sh
```

**That's it! 🎉**

---

Full docs: `/DEPLOY-PWA-NOW.md`

