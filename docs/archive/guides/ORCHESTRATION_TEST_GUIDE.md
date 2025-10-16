# 🧪 Complete Orchestration Test Guide

**Date:** October 9, 2025  
**Status:** ✅ Ready for Testing  
**URL:** `https://zoe.local/chat.html`

---

## 🚨 **ISSUES FOUND & FIXED**

### **Issue 1: Lists Expert Not Matching**
**Problem:** "show me my pending tasks" didn't match ListExpert intent patterns  
**Fix:** ✅ Added `r"pending.*task|my.*tasks|todo|get.*task"` patterns  
**Status:** Fixed and deployed

### **Issue 2: Nginx DNS Error**
**Problem:** nginx looking for `zoe-core` but container is `zoe-core-test`  
**Fix:** ✅ Updated `nginx.conf` to use `zoe-core-test:8000`  
**Status:** Fixed and deployed

### **Issue 3: No Test Data**
**Problem:** No pending tasks = no action cards shown  
**Solution:** Need to create some tasks first (see below)

---

## 🎯 **WHY YOU MIGHT NOT SEE ACTION CARDS**

Action cards **only appear** when you have:
- ✅ High-priority pending tasks, **OR**
- ✅ Upcoming birthdays in memories, **OR**
- ✅ People to call in memories

If you have **no pending tasks**, the orchestration will still work (you'll see all 5 experts), but no action cards will be shown.

---

## 📝 **HOW TO CREATE TEST DATA**

### **Option A: Through Chat (Recommended)**
1. Open `https://zoe.local/chat.html`
2. Type: "Add finish project proposal to my tasks"
3. Type: "Add review code changes to my tasks"
4. Type: "Add respond to emails to my tasks"
5. **Then** type: "Help me plan my day"

### **Option B: Direct API (If Chat Doesn't Work)**

First, create a list:
```bash
curl -X POST "https://zoe.local/api/lists/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Tasks",
    "list_type": "todo",
    "description": "My daily tasks",
    "color": "#4facfe",
    "icon": "📋"
  }'
```

Then add items (get list_id from above response):
```bash
curl -X POST "https://zoe.local/api/lists/{LIST_ID}/items" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Finish project proposal",
    "priority": "high",
    "estimated_duration": 120,
    "status": "pending"
  }'
```

---

## 🧪 **SESSIONS ISSUE - NEED MORE INFO**

You mentioned "sessions aren't working" - please clarify:

### **A. Can't Create New Sessions?**
Check in browser DevTools console for errors like:
- `422 Unprocessable Entity`
- `Missing user_id`
- `Failed to create session`

### **B. Sessions Not Saving Messages?**
Check for errors like:
- `404 Not Found` when sending messages
- Messages disappear on refresh

### **C. Can't Load Old Sessions?**
Check for:
- Sessions list not showing
- Clicking session doesn't load messages

**Please share console errors from browser DevTools (F12) so I can fix the specific issue!**

---

## 🎯 **COMPLETE TESTING CHECKLIST**

### **Step 1: Open Chat**
```
https://zoe.local/chat.html
```

Expected:
- ✅ Page loads
- ✅ Welcome screen shows
- ✅ Dynamic suggestions appear
- ✅ No console errors

### **Step 2: Create Test Data (if needed)**
Type these in chat:
```
1. "Add finish project proposal to my tasks"
2. "Add review code to my tasks"  
3. "Add respond to emails to my tasks"
```

Expected:
- ✅ Each gets added
- ✅ You see "✅ Added X to Tasks list"

### **Step 3: Trigger Orchestration**
Type:
```
Help me plan my day
```

Expected Real-Time Updates:
```
🔄 Breaking down your request...

📋 I'll coordinate 5 experts:
   1. 🗓️ Calendar expert → Get today's events and free time
   2. 📝 Lists expert → Get pending tasks
   3. ⏰ Reminder expert → Get reminders
   4. 🧠 Memory expert → Find birthdays, calls to make
   5. 📊 Planning expert → Synthesize comprehensive plan

🗓️ Calendar expert working...
   ✅ Found 9 events, 4 hours free today

📝 Lists expert working...
   ✅ Found 3 pending tasks (3 high priority)

⏰ Reminder expert working...
   ✅ Found 0 reminders for today

🧠 Memory expert working...
   ✅ Found 0 upcoming birthdays, 0 people to call

📊 Planning expert working...
   ✅ Created plan with 3 steps

💡 **Suggested Actions:**

[Interactive action cards should appear here]
```

### **Step 4: Test Action Cards**

**If you have tasks, you should see:**
```
┌─────────────────────────────────────────┐
│ 🎯 Finish project proposal (High)       │
│ High priority - Est. 120 min            │
│ [📅 Add to Calendar ▼] [⏰ Remind Me]  │
└─────────────────────────────────────────┘
```

**Click "Add to Calendar"** and you should see time slot picker expand

### **Step 5: Test Time Slot Selection**

Expected:
```
📅 Choose a time slot:

○ 10:00 AM - 12:00 PM (2h free) ✓ Fits task
○ 3:00 PM - 5:00 PM (2h free) ✓ Fits task

[Click a slot]

Schedule "Finish project proposal" for 10:00 AM - 12:00 PM?
[Cancel] [Confirm]

[Click Confirm]

✅ Added to Calendar!
```

---

## 🐛 **TROUBLESHOOTING**

### **If No Action Cards Appear:**

1. **Check if you have pending tasks:**
   - Look for "Found X pending tasks" in the orchestration output
   - If it says "Found 0 pending tasks", create some tasks first

2. **Check browser console for errors:**
   - Open DevTools (F12)
   - Look for errors in Console tab
   - Look for `action_cards` events in Network tab → chat?stream=true

3. **Verify Lists Expert Ran Successfully:**
   - Should say "✅ Found X pending tasks (Y high priority)"
   - If it says "⚠️ Could not complete this step", Lists expert failed

### **If Sessions Not Working:**

**Please provide:**
1. What specific behavior you're seeing
2. Console errors from browser DevTools
3. Network tab errors (404, 422, etc.)

**Common issues:**
- `user_id` missing from requests
- Session ID mismatch
- Backend not saving messages

---

## 🔧 **NEXT STEPS**

1. **Test through web UI** at `https://zoe.local/chat.html`
2. **Share console errors** if you see any issues
3. **Take screenshots** of what you see (or don't see)
4. **Let me know:** 
   - Are you seeing the orchestration progress?
   - Are you seeing action cards?
   - What specifically isn't working with sessions?

---

## 📊 **SYSTEM STATUS**

```
✅ Backend orchestration: WORKING (tested with curl)
✅ Multi-expert coordination: WORKING (5 experts)
✅ AG-UI streaming: WORKING (events emitted)
✅ Frontend code: DEPLOYED (v10.0)
✅ Nginx config: FIXED (zoe-core-test)
✅ All containers: RUNNING

⏳ Web UI testing: AWAITING YOUR FEEDBACK
```

---

**Ready when you are! Let's debug sessions and action cards together through the web interface.** 🚀


