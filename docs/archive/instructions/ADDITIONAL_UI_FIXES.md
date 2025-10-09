# Additional UI Fixes - Follow-up Issues

**Date**: October 9, 2025  
**Status**: ✅ All Issues Fixed

## Issues Fixed

### 🔴 **chat.html - Certificate Error on Chat Requests**

**Error Log**:
```
POST https://192.168.1.60/api/chat/enhanced?user_id=...&stream=true 
net::ERR_CERT_AUTHORITY_INVALID
```

**Root Cause**: 
- Code was building a full URL using hardcoded `API_BASE` fallback
- Line 770-771: `const apiBase = window.API_BASE || 'https://192.168.1.60/api';`
- Created absolute HTTPS URLs with self-signed certificate issues

**Fix Applied**:
```javascript
// Before
const apiBase = window.API_BASE || 'https://192.168.1.60/api';
const url = new URL(`${apiBase}/chat/enhanced`);
url.searchParams.set('user_id', session?.user_id || 'default');
url.searchParams.set('stream', 'true');

// After
const url = `/api/chat/enhanced?user_id=${session?.user_id || 'default'}&stream=true`;
```

**Files Modified**: 
- `services/zoe-ui/dist/chat.html` (lines 768-784)

**Impact**: Chat now works without certificate errors

---

### 🟡 **chat.html - Missing API Status Indicator**

**Problem**: API status indicator wasn't showing connection state

**Fix Applied**: Added proper CSS class to API indicator element
```html
<!-- Before -->
<div class="api-indicator" id="apiStatus">Connecting</div>

<!-- After -->
<div class="api-indicator connecting" id="apiStatus">Connecting</div>
```

**Files Modified**: 
- `services/zoe-ui/dist/chat.html` (line 553)

**Impact**: Users can now see if backend is online/offline on chat page

---

### 🔴 **memories.html - CORS Errors from Direct Localhost Calls**

**Error Logs**:
```
GET http://localhost:8010/people net::ERR_FAILED
Access to fetch ... has been blocked by CORS policy

GET http://localhost:8011/collections net::ERR_FAILED
Access to fetch ... has been blocked by CORS policy
```

**Root Cause**:
- Code was bypassing nginx proxy and calling microservices directly
- Line 1377: `fetch('http://localhost:8010/people')`
- Line 1416: `fetch('http://localhost:8011/collections')`
- Line 1431: `fetch('http://localhost:8011/collections/${id}/tiles')`

**Fix Applied**:
```javascript
// Before - Direct localhost calls
const response = await fetch('http://localhost:8010/people', {
    headers: session ? { 'Authorization': `Bearer ${session.token}` } : {}
});

// After - Use nginx proxy via apiRequest helper
const response = await apiRequest('/people');
```

**Changes Made**:
1. **loadPeople()** - Now uses `apiRequest('/people')` instead of direct fetch
2. **loadCollections()** - Now uses `apiRequest('/collections')` instead of direct fetch
3. **Load tiles loop** - Now uses `apiRequest(`/collections/${id}/tiles`)` instead of direct fetch

**Files Modified**: 
- `services/zoe-ui/dist/memories.html` (lines 1372-1446)

**Impact**: Memories page now loads without CORS errors

---

### 🔴 **ai-processor.js - Hardcoded IP Address Fallback**

**Error Log**:
```
GET https://192.168.1.60/api/memories/?type=people 
net::ERR_CERT_AUTHORITY_INVALID
```

**Root Cause**:
- AI processor had hardcoded fallback IP address
- Line 106: `return window.API_BASE || 'https://192.168.1.60/api';`

**Fix Applied**:
```javascript
// Before
get API_BASE() {
    return window.API_BASE || 'https://192.168.1.60/api';
}

// After
get API_BASE() {
    return window.API_BASE || '/api';
}
```

**Files Modified**: 
- `services/zoe-ui/dist/js/ai-processor.js` (line 106)

**Impact**: AI processor now uses relative URLs, no certificate errors

---

## Backend Issues (Not UI Bugs)

### ⚠️ **Reminders Endpoint 500 Errors**

**Error Logs** (lists.html, calendar.html):
```
GET https://zoe.local/api/reminders/ 500 (Internal Server Error)
```

**Status**: **Backend Issue** - Not a UI bug  
**Explanation**: 
- The UI is correctly making the API request
- The backend `/api/reminders/` endpoint is returning 500 error
- This needs to be fixed in the backend service

**Recommendation**: Check zoe-core reminders endpoint for backend errors

---

## Summary of Changes

### Files Modified:
1. ✅ `services/zoe-ui/dist/chat.html` - Fixed hardcoded URL and added API status
2. ✅ `services/zoe-ui/dist/memories.html` - Fixed direct localhost calls to use nginx proxy
3. ✅ `services/zoe-ui/dist/js/ai-processor.js` - Fixed hardcoded IP fallback

### Issues Resolved:
- ✅ Chat certificate errors (HTTPS with self-signed cert)
- ✅ Chat API status indicator not showing
- ✅ Memories CORS errors (people service)
- ✅ Memories CORS errors (collections service)
- ✅ AI processor certificate errors

### Remaining Backend Issues:
- ⚠️ `/api/reminders/` endpoint returning 500 errors (backend fix needed)

---

## Testing Results

### ✅ Works Now:
- Chat page loads and sends messages without certificate errors
- Chat page shows API connection status
- Memories page loads people without CORS errors
- Memories page loads collections without CORS errors
- AI processor works without certificate errors

### ⚠️ Still Issues (Backend):
- Reminders endpoint needs backend fix for 500 error
- Lists and Calendar pages affected by reminders endpoint error

---

## Next Steps

1. **Backend Team**: Fix `/api/reminders/` endpoint 500 error in zoe-core
2. **Test**: Verify all pages work after backend fix
3. **Deploy**: All UI fixes are ready for production

All client-side issues are now resolved! 🎉
