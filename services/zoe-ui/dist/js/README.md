# Zoe UI JavaScript Modules

## Core Modules

### auth.js
**Centralized Authentication System**

Handles all authentication for Zoe UI pages.

**Features:**
- Auto-injects `X-Session-ID` header on all fetch requests
- Strips legacy `user_id` query parameters
- Auto-redirects to login on 401 (session expired)
- Provides global `window.ZoeAuth` API

**Usage:**
```html
<script src="/js/auth.js"></script>
```

**API:**
```javascript
// Check if authenticated
if (ZoeAuth.isAuthenticated()) {
    console.log('User is logged in');
}

// Get current session ID
const sessionId = ZoeAuth.getSession();

// Logout
ZoeAuth.logout();

// Set session (after login)
ZoeAuth.setSession('session-id-here');
```

**Automatic Behavior:**
- All `fetch()` calls automatically include authentication
- No manual header configuration needed
- Transparent to existing code

## Deployment

All HTML pages automatically load `auth.js` via:
```html
<script src="/js/auth.js"></script>
```

Deployed to 34 pages on 2025-09-30.

## Maintenance

**Update auth system:**
1. Edit `/services/zoe-ui/dist/js/auth.js`
2. Restart `zoe-ui` container (changes are live immediately)

**Add to new pages:**
```html
<head>
    ...
    <script src="/js/auth.js"></script>
</head>
```

## Security

- Session stored in `localStorage` as `zoe_session`
- Session validated server-side on every API request
- Auto-logout on 401/403 responses
- No credentials stored client-side (only session ID)
