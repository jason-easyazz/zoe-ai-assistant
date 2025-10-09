# ISSUE: Persistent Mixed Content Errors in Chat Interface

## Status: UNRESOLVED (Need Help)
**Severity:** High  
**Component:** Frontend (chat.html, auth.js, common.js)  
**Date:** October 9, 2025  

---

## Problem Summary

Despite multiple fix attempts (v6.1 through v6.5), **mixed content errors persist** when the chat interface loads:

```
Mixed Content: The page at 'https://zoe.local/chat.html' was loaded over HTTPS, 
but requested an insecure resource 'http://zoe.local/api/calendar/events?...'
This request has been blocked; the content must be served over HTTPS.
```

## Critical Observation

**The auth.js interceptor debug logs are NOT appearing in the console**, which means:
- The `window.fetch` interceptor is NOT catching these requests
- The HTTP→HTTPS conversion code is NOT running
- Something is bypassing the interceptor entirely

**Expected debug logs (NOT seen):**
```javascript
🔒 Forced HTTP → HTTPS: ...
📍 HTTPS → Relative: ...
```

**What IS appearing:**
```
Making API request to: /api/calendar/events/?user_id=...  (from common.js)
Mixed Content: ...requested 'http://zoe.local/api/calendar/events...'
```

---

## Current Implementation

### File: `/home/pi/zoe/services/zoe-ui/dist/js/auth.js`

**Fetch Interceptor (Lines 195-241):**
```javascript
const originalFetch = window.fetch;
function setupFetchInterceptor() {
    window.fetch = function(url, options = {}) {
        // Extract URL string (handle Request objects)
        let urlString = url;
        if (url instanceof Request) {
            urlString = url.url;
        }
        
        if (typeof urlString === 'string') {
            // FORCE HTTPS
            if (urlString.startsWith('http://')) {
                urlString = urlString.replace(/^http:\/\//, 'https://');
                console.log('🔒 Forced HTTP → HTTPS:', urlString);  // NOT APPEARING!
            }
            
            // Convert to relative
            if (urlString.startsWith('https://')) {
                const relativePath = urlString.replace(/^https:\/\/[^/]+/, '');
                console.log('📍 HTTPS → Relative:', relativePath);  // NOT APPEARING!
                urlString = relativePath;
            }
            
            // Remove user_id
            // ... (working, as evidenced by clean URLs in successful requests)
            
            url = urlString;
        }
        
        return originalFetch(url, options).then(response => {
            // ... handle 401, etc
        });
    };
}
```

**Initialization:**
```javascript
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        setupFetchInterceptor();
        enforceAuth();
    });
} else {
    setupFetchInterceptor();
    enforceAuth();
}
```

### File: `/home/pi/zoe/services/zoe-ui/dist/js/common.js`

**apiRequest function (Lines 156-225):**
```javascript
async function apiRequest(endpoint, options = {}) {
    try {
        // Determine service URL
        let serviceUrl = SERVICE_MAP['default'];  // Returns '/api'
        let normalizedEndpoint = endpoint;
        
        // If endpoint already starts with /api, use as-is
        if (endpoint.startsWith('/api/')) {
            serviceUrl = '';
            normalizedEndpoint = endpoint;
        }
        
        const fullUrl = `${serviceUrl}${normalizedEndpoint}`;
        console.log('Making API request to:', fullUrl);
        
        // ... add headers, session ID, etc
        
        const response = await fetch(fullUrl, options);  // ← CALLS INTERCEPTED fetch()
        
        return await response.json();
    } catch (error) {
        console.error('API request error:', error);
        throw error;
    }
}
```

### File: `/home/pi/zoe/services/zoe-ui/dist/chat.html`

**Dynamic Suggestions (Lines 1521-1526):**
```javascript
const [calendarData, tasksData, memoriesData] = await Promise.allSettled([
    apiRequest(`/api/calendar/events/?user_id=${userId}&start_date=${startDate}&end_date=${endDate}`).catch(e => ({events: []})),
    apiRequest(`/api/lists/?user_id=${userId}&list_name=personal_todos`).catch(e => ({items: []})),
    apiRequest(`/api/memories/proxy/people/?user_id=${userId}`).catch(e => ({people: []}))
]);
```

---

## Browser Console Evidence

### What We See:

1. **Version confirmed:** ✅
   ```
   🔄 Chat.html v6.5 - Force HTTPS + Debug Logging
   ```

2. **Auth initialized:** ✅
   ```
   ✅ Zoe Auth initialized (DOMContentLoaded)
   ```

3. **Requests logged by common.js:** ✅
   ```
   Making API request to: /api/calendar/events/?user_id=...&start_date=...&end_date=...
   ```

4. **Mixed content error:** ❌
   ```
   Mixed Content: ...requested an insecure resource 'http://zoe.local/api/calendar/events?...'
   ```

5. **Interceptor debug logs:** ❌ **NOT APPEARING**
   ```
   🔒 Forced HTTP → HTTPS: ...  ← MISSING
   📍 HTTPS → Relative: ...     ← MISSING
   ```

### What Works:

- Core chat sessions: ✅ (200 OK, HTTPS)
- Session messages: ✅ (200 OK, HTTPS)
- Message creation: ✅

### What Fails:

- Calendar events: ❌ (Mixed content, HTTP)
- Lists API: ❌ (404, but reaching server via HTTPS)
- Memories proxy: ❌ (Mixed content, HTTP)

---

## Hypotheses

### 1. **Fetch Interceptor Not Running** (Most Likely)
- The interceptor setup might be running too late
- Or being overridden by something else
- Or not intercepting certain types of fetch calls

### 2. **Browser Converting URLs Before Interceptor**
- The browser might be converting relative URLs → absolute HTTP URLs
- This happens BEFORE the interceptor sees them
- Based on `<base>` tag, window.location, or mixed content from elsewhere

### 3. **Request Objects Bypassing String Checks**
- If fetch is called with a Request object created elsewhere
- And that Request has an `http://` URL
- Our `url instanceof Request` check extracts the URL but might be too late

### 4. **Service Worker or Other Interceptor**
- Something else might be intercepting fetch calls first
- Converting URLs before our interceptor runs

---

## Questions for Investigation

1. **Is the fetch interceptor actually installed?**
   - Check: `console.log(window.fetch.toString())` in browser console
   - Should show our custom function, not native code

2. **What's the execution order?**
   - Is `setupFetchInterceptor()` called before `apiRequest()`?
   - Add more console logs to trace execution

3. **Are there any `<base>` tags in the HTML?**
   - Check for `<base href="http://...">`
   - This would force all relative URLs to HTTP

4. **Is there a Service Worker active?**
   - Check in DevTools → Application → Service Workers
   - Service workers can intercept fetch calls

5. **What does `window.location` report?**
   - If it's `http://` instead of `https://`, that could be the issue

---

## Diagnostic Commands

```javascript
// Run in browser console:

// 1. Check if interceptor is installed
console.log('Fetch type:', typeof window.fetch);
console.log('Fetch source:', window.fetch.toString().substring(0, 200));

// 2. Check page protocol
console.log('Page protocol:', window.location.protocol);
console.log('Page href:', window.location.href);

// 3. Check for base tag
console.log('Base tags:', document.querySelectorAll('base'));

// 4. Check for service workers
navigator.serviceWorker.getRegistrations().then(r => console.log('Service workers:', r));

// 5. Manual test
fetch('/api/test').then(r => console.log('Manual fetch worked'));
```

---

## Attempted Solutions (All Failed)

| Version | Approach | Result |
|---------|----------|--------|
| v6.1 | Add `/api/` prefix | ✅ Fixed endpoint paths |
| v6.2 | Use `apiRequest()` instead of direct `fetch()` | ✅ Better structure |
| v6.3 | Position-aware user_id removal | ✅ Clean URLs |
| v6.4 | Convert absolute → relative | ❌ Mixed content persists |
| v6.5 | Force HTTP → HTTPS → relative | ❌ Mixed content persists (interceptor not running) |

---

## Proposed Solutions (Need Help)

### Option 1: Debug Why Interceptor Not Running
- Add extensive logging to trace execution order
- Check if something is overriding `window.fetch` after we set it
- Verify interceptor runs before first API call

### Option 2: Different Interception Point
- Use XMLHttpRequest interceptor instead of fetch
- Or use a Proxy on fetch
- Or patch at a different level

### Option 3: Force HTTPS at Source
- Modify `apiRequest()` in common.js to force HTTPS
- Before calling fetch, convert any detected HTTP to HTTPS
- Don't rely on the interceptor

### Option 4: Disable Dynamic Suggestions (Temporary)
- Comment out the calendar/lists/memories API calls
- Let the page load without errors
- Focus on core chat functionality first

### Option 5: Use Full HTTPS URLs
- Instead of relative `/api/...`, use full `https://zoe.local/api/...`
- Might bypass whatever is converting to HTTP
- But less elegant

---

## Files Involved

1. `/home/pi/zoe/services/zoe-ui/dist/js/auth.js` - Fetch interceptor
2. `/home/pi/zoe/services/zoe-ui/dist/js/common.js` - apiRequest function
3. `/home/pi/zoe/services/zoe-ui/dist/chat.html` - Dynamic suggestions

---

## Success Criteria

✅ **No mixed content errors in browser console**  
✅ **All API requests use HTTPS or relative URLs**  
✅ **Debug logs appear showing URL transformations**  
✅ **Chat functionality works without security warnings**  

---

## Request for Help

**Cursor Web Agent / Codex:** Please investigate:

1. **Why is the fetch interceptor not catching these requests?**
   - The debug logs (🔒 and 📍) never appear
   - But the interceptor should be installed by DOMContentLoaded

2. **What's converting `/api/...` to `http://zoe.local/api/...`?**
   - This happens between common.js logging the request
   - And the browser making the actual HTTP request

3. **What's the correct way to force HTTPS for all fetch calls?**
   - Current approach isn't working
   - Need a more robust solution

4. **Should we use a different architecture?**
   - Maybe fetch interceptors aren't reliable enough
   - Alternative approaches?

---

**Status:** Blocked, need architectural guidance  
**Priority:** High (security warnings in production)  
**Impact:** Chat loads and works, but shows security warnings

