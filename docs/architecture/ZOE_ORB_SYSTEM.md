# Zoe Orb System - Architecture & Implementation

**Date:** October 9, 2025  
**Status:** ✅ Active on all pages

## Overview

The **Zoe Orb** is a persistent AI chat interface that appears on all Zoe pages as a floating button in the bottom-right corner. It provides quick access to conversational AI without navigating away from the current page.

---

## Architecture

### Component Structure

**Location:** `/home/zoe/assistant/services/zoe-ui/dist/components/zoe-orb.html`

The Orb is a self-contained HTML component that includes:
- **CSS** - All styling and animations
- **HTML** - Orb button and chat window
- **JavaScript** - Chat functionality and WebSocket connection

### Component Loading

Pages dynamically load the orb component via JavaScript:

```javascript
fetch("/components/zoe-orb.html")
  .then(r => r.text())
  .then(h => {
    const d = document.createElement("div");
    d.innerHTML = h;
    while(d.firstChild) {
      document.body.appendChild(d.firstChild);
    }
  });
```

**Pages with Orb:**
- ✅ Dashboard (`dashboard.html`)
- ✅ Calendar (`calendar.html`)
- ✅ Lists (`lists.html`)
- ✅ Journal (`journal.html`)
- ✅ Memories (`memories.html`)
- ✅ Workflows (`workflows.html`)
- ✅ Settings (`settings.html`)
- ❌ Chat (uses dedicated chat interface instead)

---

## Features

### 1. Visual States

The orb changes appearance based on its status:

| State | Gradient | Animation | Meaning |
|-------|----------|-----------|---------|
| **Default** | Purple → Violet | Liquid swirl + breathe | Ready to chat |
| **Connecting** | Purple → Indigo | Liquid swirl | Connecting to WebSocket |
| **Connected** | Purple → Green | Liquid swirl | WebSocket active |
| **Thinking** | Purple → Amber | Rotate + swirl | Processing message |
| **Chatting** | Purple → Cyan | Enhanced swirl | Chat window open |
| **Error** | Purple → Red | Shake + swirl | Connection failed |
| **Proactive** | Purple → Pink | Pulse + swirl | Has suggestion |

### 2. Chat Interface

**Features:**
- Floating chat window (320px × 420px)
- Auto-resizing textarea (up to 120px height)
- Message history display
- Typing indicators
- SSE streaming responses
- Context preservation

**Chat API Endpoint:**
```
POST /api/chat/?user_id={user_id}&stream=true
```

**Request Format:**
```json
{
  "message": "User's message",
  "context": {},
  "mode": "orb_chat",
  "session_id": "orb_{timestamp}"
}
```

**Response:** Server-Sent Events (SSE) stream
```
data: {"type": "token", "content": "token text"}
data: {"type": "token", "content": " more text"}
data: {"type": "done", "context": {...}}
```

### 3. Intelligence WebSocket

**Purpose:** Proactive suggestions and notifications

**Endpoint:** `wss://zoe.local/api/ws/intelligence`

**Status:** ⚠️ **Not Implemented**
- The frontend attempts to connect
- Connection fails gracefully with retry logic
- Does not affect core functionality
- Falls back to chat-only mode

**Event Types (When Implemented):**
```javascript
{
  "type": "proactive_suggestion",
  "data": {
    "message": "Suggestion text",
    "priority": "medium"
  }
}
```

**Retry Logic:**
- Max retries: 2
- Exponential backoff: 1s, 2s, max 10s
- After max retries: Silent fallback to chat-only mode

---

## Implementation Guide

### Adding the Orb to a New Page

**Option 1: Dynamic Loading (Recommended)**

Add this script at the end of your HTML body:

```html
<script>
if (!document.getElementById("zoeOrb")) {
    fetch("/components/zoe-orb.html")
        .then(r => r.text())
        .then(h => {
            const d = document.createElement("div");
            d.innerHTML = h;
            while(d.firstChild) {
                document.body.appendChild(d.firstChild);
            }
        });
}
</script>
```

**Option 2: Direct Inline (Dashboard Method)**

Copy the entire orb HTML/CSS/JS from `components/zoe-orb.html` directly into your page.

### Customizing Orb Behavior

**Change Chat Endpoint:**
```javascript
// In sendOrbMessage function
const response = await fetch(`/api/chat/your-custom-endpoint`, {...});
```

**Disable WebSocket:**
```javascript
// Comment out in initOrbChat()
// initIntelligenceWS();
```

**Custom Styling:**
```css
.zoe-orb {
    bottom: 24px;  /* Position */
    right: 24px;
    width: 70px;   /* Size */
    height: 70px;
}
```

---

## API Integration

### Required Backend Endpoints

**1. Chat Endpoint (REQUIRED)**
```
POST /api/chat/?user_id={user_id}&stream=true
- Accepts JSON message payload
- Returns SSE stream
- Status: ✅ Implemented
```

**2. Intelligence WebSocket (OPTIONAL)**
```
WS /api/ws/intelligence
- Sends proactive suggestions
- Handles presence updates
- Status: ⚠️ Not Implemented (gracefully ignored)
```

### Example Backend Implementation

```python
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

@router.post("/chat/")
async def chat(request: ChatRequest):
    async def generate():
        yield f"data: {json.dumps({'type': 'token', 'content': 'Hello'})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")
```

---

## Replication Guide

### To Add Orb to Custom Pages

**Step 1:** Copy the component
```bash
cp /home/zoe/assistant/services/zoe-ui/dist/components/zoe-orb.html your-custom-page-directory/
```

**Step 2:** Add loader script to your page
```html
<!-- At end of body -->
<script>
fetch("/components/zoe-orb.html").then(r=>r.text()).then(h=>{
    const d=document.createElement("div");
    d.innerHTML=h;
    while(d.firstChild){
        document.body.appendChild(d.firstChild);
    }
});
</script>
```

**Step 3:** Ensure authentication
- Pages should include `auth.js`
- Auth provides session context for API calls

### To Modify Orb Globally

**Edit:** `/home/zoe/assistant/services/zoe-ui/dist/components/zoe-orb.html`

**Changes auto-apply to:**
- All pages that dynamically load the component
- New page loads (cached briefly by browser)

**Force refresh:**
```bash
# Clear browser cache or add version query
fetch("/components/zoe-orb.html?v=2")
```

---

## WebSocket Error Resolution

### Current Behavior

**Console Warning:**
```
WebSocket connection to 'wss://zoe.local/ws/intelligence' failed
```

**Impact:** ⚠️ **None** - Cosmetic only
- Chat functionality works perfectly
- SSE streaming works
- All features except proactive suggestions available

### Option 1: Implement WebSocket Endpoint

```python
from fastapi import WebSocket

@router.websocket("/api/ws/intelligence")
async def intelligence_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Send proactive suggestions
            await websocket.send_json({
                "type": "proactive_suggestion",
                "data": {"message": "Time for a break!"}
            })
            await asyncio.sleep(3600)  # Every hour
    except:
        await websocket.close()
```

### Option 2: Disable WebSocket Connection

**Edit:** `components/zoe-orb.html`

```javascript
// In initOrbChat() function, comment out:
// initIntelligenceWS();
```

### Option 3: Leave As-Is (Recommended)

The current implementation is **graceful**:
- Retries 2 times with exponential backoff
- Then silently falls back
- No user-facing impact
- Ready for future WebSocket implementation

---

## Troubleshooting

### Orb Not Appearing

**Check 1:** Component loaded?
```javascript
console.log(document.getElementById('zoeOrb'));
```

**Check 2:** Component file accessible?
```bash
curl http://localhost/components/zoe-orb.html
```

**Check 3:** JavaScript errors?
- Open browser console
- Look for fetch/parse errors

### Chat Not Working

**Check 1:** API endpoint accessible?
```bash
curl -X POST http://localhost:8000/api/chat/?stream=true
```

**Check 2:** Authentication working?
```javascript
console.log(window.zoeAuth?.getCurrentSession());
```

**Check 3:** CORS headers correct?
- SSE requires proper CORS
- Check nginx configuration

### WebSocket Always Failing

**Expected:** This is normal if `/ws/intelligence` not implemented

**Verify:**
```bash
# Should return 404 or connection error
wscat -c wss://zoe.local/api/ws/intelligence
```

**Solution:** Either implement endpoint or disable connection (see above)

---

## Performance Considerations

### Load Time

- **Component size:** ~11KB (HTML + CSS + JS)
- **Load method:** Async fetch (non-blocking)
- **Cache:** Browser caches component

### Runtime

- **Memory:** ~2MB (chat history)
- **CPU:** Minimal (animations use GPU)
- **Network:** SSE stream when chatting only

### Optimization Tips

```javascript
// Lazy load only when needed
let orbLoaded = false;
document.addEventListener('click', () => {
    if (!orbLoaded) loadOrb();
}, { once: true });
```

---

## Future Enhancements

### Planned Features

1. **Intelligence WebSocket** - Proactive suggestions
2. **Voice Input** - Speech-to-text in chat
3. **Quick Actions** - One-click tasks from orb
4. **Multi-Context** - Different contexts per page
5. **Offline Mode** - Cached responses when offline

### Extension Points

The orb is designed to be extensible:

```javascript
// Custom event handlers
window.orbEventHandlers = {
    onOpen: () => { /* Custom logic */ },
    onMessage: (msg) => { /* Process message */ },
    onClose: () => { /* Cleanup */ }
};
```

---

## Related Documentation

- **AG-UI Streaming:** `/docs/architecture/AG_UI_STREAMING.md`
- **Chat API:** `/docs/api/CHAT_ENDPOINTS.md`
- **Authentication:** `/docs/architecture/AUTH_INTEGRATION.md`

---

## Summary

**✅ Zoe Orb is production-ready** and deployed on all main pages  
**✅ Chat functionality works perfectly** via SSE streaming  
**⚠️ WebSocket warnings are cosmetic** and can be ignored  
**📝 Component is reusable** and easy to add to new pages  
**🔧 Fully customizable** via single component file  


