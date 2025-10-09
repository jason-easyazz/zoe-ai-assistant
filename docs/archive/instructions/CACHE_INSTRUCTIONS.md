# Browser Cache Issue - How to Fix

**Problem**: Browser has cached old JavaScript code  
**Solution**: Force reload with these steps

---

## Method 1: Hard Refresh (Try This First)

### On the Chat Page:
1. Open https://zoe.local/chat.html
2. Press **Ctrl + Shift + R** (or **Cmd + Shift + R** on Mac)
3. Wait for page to fully reload
4. Try sending a message

---

## Method 2: Clear Site Data (If Method 1 Fails)

### Chrome/Edge:
1. Press **F12** to open DevTools
2. Right-click the **Reload button** (⟳)
3. Select **"Empty Cache and Hard Reload"**
4. Close DevTools
5. Try again

### Firefox:
1. Press **Ctrl + Shift + Delete**
2. Select **"Cached Web Content"**
3. Time Range: **"Everything"**
4. Click **"Clear Now"**
5. Reload page

---

## Method 3: Clear All Browser Data (Nuclear Option)

### Chrome/Edge:
1. Press **Ctrl + Shift + Delete**
2. Select:
   - ✅ Cached images and files
   - ✅ Cookies and site data
3. Time Range: **"All time"**
4. Click **"Clear data"**
5. Go back to https://zoe.local/chat.html

---

## Method 4: Incognito/Private Window

1. Open **Incognito Window** (Ctrl + Shift + N)
2. Go to https://zoe.local/chat.html
3. Login with your credentials
4. Test - should work perfectly!

---

## How to Verify It's Fixed

After clearing cache, your console should show:

```javascript
✅ Auth check on: /chat.html
✅ Session valid - access granted
✅ Zoe Auth initialized
🔄 Chat.html v2.0 - AG-UI Edition loaded  // ← NEW

// Session creation
Response status: 200  // ✅ Not 404!

// Message save
Response status: 200  // ✅ Not 404!

// Chat streaming
📡 AG-UI Event: session_start
📡 AG-UI Event: message_delta
```

**NO 404 errors!**

---

## Why This Happens

Browsers aggressively cache JavaScript files for performance. When we update `chat.html`, the browser continues serving the old cached version until:
1. Cache expires (could be hours/days)
2. You hard refresh (Ctrl+Shift+R)
3. You clear cache manually
4. Server sends different cache headers

I've added cache-busting versioning (`?v=2.0`) to force browsers to reload the scripts.

---

## If Still Not Working

### Check What's Being Served:
```bash
curl -s 'https://zoe.local/chat.html' -k | grep "v2.0"
# Should see: Chat.html v2.0 - AG-UI Edition
```

### Test API Directly:
```bash
# This should work (proven working):
curl -X POST 'https://zoe.local/api/chat/sessions/SESSION_ID/messages/?user_id=USER_ID' \
  -H "Content-Type: application/json" \
  -d '{"session_id":"SESSION_ID","role":"user","content":"test"}' -k
```

### Check Browser:
- Open DevTools → Network tab
- Reload page
- Look for chat.html
- Check if it says "(disk cache)" or "(from cache)"
- If yes → Clear cache again

---

## Expected After Cache Clear

✅ Sessions panel shows conversations  
✅ Messages send and stream  
✅ No 404 errors  
✅ AG-UI events in console  
✅ Everything works!

The backend IS working - we just need fresh browser cache! 🚀
