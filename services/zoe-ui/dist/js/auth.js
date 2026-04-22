/**
 * Zoe Authentication System
 * Centralized auth for all UI pages
 */

(function() {
    'use strict';
    
    const AUTH_CONFIG = {
        sessionKey: 'zoe_session',
        authBaseUrl: '/api/auth'  // Use relative URL for nginx proxy
    };
    const TOUCH_PATH_TO_PAGE_ID = {
        '/touch/dashboard.html': 'dashboard',
        '/touch/calendar.html': 'calendar',
        '/touch/lists.html': 'lists',
        '/touch/chat.html': 'chat',
        '/touch/weather.html': 'weather',
        '/touch/smarthome.html': 'smarthome',
        '/touch/smart-home.html': 'smarthome',
        '/touch/timers.html': 'timers',
        '/touch/music.html': 'music',
        '/touch/notes.html': 'notes',
        '/touch/journal.html': 'journal',
        '/touch/people.html': 'people',
        '/touch/memories.html': 'memories',
        '/touch/settings.html': 'settings',
        '/touch/updates.html': 'updates',
    };
    let _capabilityMatrix = null;

    // Get session ID from localStorage
    function getSession() {
        const sessionStr = localStorage.getItem(AUTH_CONFIG.sessionKey);
        if (!sessionStr) return '';
        
        try {
            const session = JSON.parse(sessionStr);
            return session.session_id || '';
        } catch (error) {
            return '';
        }
    }

    // Get full session object from localStorage
    function getSessionObject() {
        const sessionStr = localStorage.getItem(AUTH_CONFIG.sessionKey);
        if (!sessionStr) return null;
        
        try {
            return JSON.parse(sessionStr);
        } catch (error) {
            return null;
        }
    }

    // Store session in localStorage
    function setSession(sessionData) {
        if (sessionData) {
            // If it's a string, assume it's session_id and create object
            if (typeof sessionData === 'string') {
                const sessionObj = { session_id: sessionData };
                localStorage.setItem(AUTH_CONFIG.sessionKey, JSON.stringify(sessionObj));
                console.log('💾 Session stored (from string):', sessionObj);
            } else {
                // Store full session object
                localStorage.setItem(AUTH_CONFIG.sessionKey, JSON.stringify(sessionData));
                console.log('💾 Session stored (object):', sessionData);
            }
        } else {
            localStorage.removeItem(AUTH_CONFIG.sessionKey);
            console.log('🗑️ Session cleared');
        }
    }

    // Check if user is authenticated
    function isAuthenticated() {
        const session = getSessionObject();
        if (!session) return false;
        
        // Check if session is expired
        if (session.expires_at) {
            const expiresAt = new Date(session.expires_at);
            const now = new Date();
            if (now >= expiresAt) {
                return false;
            }
        }
        
        return !!session.session_id;
    }

    function isGuestSession() {
        const session = getSessionObject();
        if (!session) return false;
        const role = String(session.role || session.user_info?.role || '').toLowerCase();
        const userId = String(session.user_id || session.user_info?.user_id || '').toLowerCase();
        return role === 'guest' || userId === 'guest';
    }

    function isAuthenticatedNonGuestSession() {
        const session = getSessionObject();
        if (!session || !session.session_id) return false;
        const role = String(session.role || session.user_info?.role || '').toLowerCase();
        const userId = String(session.user_id || session.user_info?.user_id || '').toLowerCase();
        if (role) return role !== 'guest';
        // Some session payloads omit role; in that case trust non-guest user_id.
        return !!userId && userId !== 'guest';
    }

    async function bootstrapTouchGuestSession() {
        const currentPath = window.location.pathname;
        if (!currentPath.startsWith('/touch/')) return;
        if (currentPath === '/touch/index.html') return;
        if (isAuthenticated()) return;
        try {
            const response = await originalFetch('/api/auth/guest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_info: { source: 'touch-auth-bootstrap' } }),
            });
            if (!response.ok) return;
            const data = await response.json();
            if (data && data.session_id) {
                setSession(data);
                try { localStorage.setItem('zoe_guest_mode', 'active'); } catch (_) {}
            }
        } catch (_) {
            try { localStorage.setItem('zoe_guest_mode', 'degraded'); } catch (_) {}
        }
    }

    function getPageIdFromPath(pathname) {
        return TOUCH_PATH_TO_PAGE_ID[pathname] || null;
    }

    async function loadCapabilityMatrix() {
        if (_capabilityMatrix) return _capabilityMatrix;
        try {
            const response = await originalFetch('/api/system/capability-matrix/me');
            if (response.ok) {
                const data = await response.json();
                if (data && data.matrix) {
                    _capabilityMatrix = data;
                    try { localStorage.setItem('zoe_capability_matrix', JSON.stringify(data)); } catch (_) {}
                    return _capabilityMatrix;
                }
            }
        } catch (_) {}
        try {
            const cached = localStorage.getItem('zoe_capability_matrix');
            if (cached) {
                _capabilityMatrix = JSON.parse(cached);
                return _capabilityMatrix;
            }
        } catch (_) {}
        return null;
    }

    // Get current user info from session
    async function getCurrentUser() {
        const sessionId = getSession();
        if (!sessionId) return null;
        
        try {
            const response = await originalFetch(`${AUTH_CONFIG.authBaseUrl}/profile`, {
                headers: {
                    'X-Session-ID': sessionId
                }
            });
            
            if (response.ok) {
                const data = await response.json();
                return data;
            }
            return null;
        } catch (error) {
            console.error('Error fetching user profile:', error);
            return null;
        }
    }

    // Get current session object
    function getCurrentSession() {
        return getSessionObject() || { session_id: null };
    }

    // Logout
    function logout(delay = 0) {
        console.warn('🚪 Logout called');

        // Capture the session id BEFORE we clear local state so we can tell
        // the server to invalidate it. This is fire-and-forget: even if the
        // server is unreachable, we still want to wipe the client session.
        const current = getSessionObject();
        const sid = current && current.session_id;
        if (sid) {
            try {
                fetch('/api/auth/logout', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Session-ID': sid
                    },
                    keepalive: true
                }).catch((err) => console.warn('Logout request failed:', err));
            } catch (err) {
                console.warn('Logout request threw:', err);
            }
        }

        setSession(null);

        const currentPath = window.location.pathname;
        const redirectUrl = currentPath.startsWith('/touch/') ? '/touch/index.html' : '/index.html';

        if (delay > 0) {
            console.warn(`⏳ Redirecting to ${redirectUrl} in ${delay}ms`);
            setTimeout(() => { window.location.href = redirectUrl; }, delay);
        } else {
            console.warn(`⚡ Redirecting to ${redirectUrl} immediately`);
            window.location.href = redirectUrl;
        }
    }

    // Check if user should be on a protected page
    async function enforceAuth() {
        const currentPath = window.location.pathname;
        const search = window.location.search || '';
        // Persist kiosk mode in localStorage so navigations within the touch UI
        // don't lose it (voice commands navigate to /touch/calendar.html etc. without query params).
        if (currentPath.startsWith('/touch/') && search.includes('kiosk=1')) {
            try { localStorage.setItem('zoe_kiosk', '1'); } catch(_){}
        }
        const kioskStored = currentPath.startsWith('/touch/') && (function(){ try { return localStorage.getItem('zoe_kiosk') === '1'; } catch(_){ return false; } })();
        const isKioskMode = kioskStored || (currentPath.startsWith('/touch/') && search.includes('kiosk=1'));
        const isPublicGameModule =
            currentPath === '/modules/qd' ||
            currentPath.startsWith('/modules/qd/') ||
            currentPath === '/modules/jag-board' ||
            currentPath.startsWith('/modules/jag-board/') ||
            currentPath === '/modules/orbit' ||
            currentPath.startsWith('/modules/orbit/');
        // Only allow the main login pages and auth.html to skip authentication
        const isLoginPage = currentPath === '/index.html' || 
                           currentPath === '/' || 
                           currentPath === '/touch/index.html';
        const isAuthPage = currentPath === '/auth.html';
        
        // Skip auth check for login/auth and public game modules only.
        if (isLoginPage || isAuthPage || isPublicGameModule) {
            console.log('✅ Auth check skipped - on login/auth page');
            return;
        }
        
        // Get session for debugging
        const session = getSessionObject();
        console.log('🔐 Auth check on:', currentPath);
        console.log('📦 Session data:', session ? '(exists)' : '(none)');
        
        // Check if user has valid session using isAuthenticated
        if (!isAuthenticated()) {
            // Kiosk degraded mode: keep touch surface available for household-safe
            // operations even if guest token bootstrap failed.
            if (isKioskMode && currentPath.startsWith('/touch/')) {
                console.warn('⚠️ No session in kiosk mode; allowing degraded guest surface');
                return;
            }
            console.warn('⚠️ No valid session - redirecting to login');
            
            // Show user-friendly message
            const showError = () => {
                const errorDiv = document.createElement('div');
                errorDiv.style.cssText = `
                    position: fixed;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    background: rgba(0,0,0,0.95);
                    color: white;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    z-index: 999999;
                    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
                    padding: 20px;
                `;
                const _fromTouch = window.location.pathname.startsWith('/touch/');
                const _loginDest = _fromTouch ? '/touch/index.html' : '/index.html';
                // Store the page we were trying to visit so login can redirect back
                try { sessionStorage.setItem('zoe_redirect_after_login', window.location.pathname + window.location.search); } catch(_){}
                errorDiv.innerHTML = `
                    <div style="text-align: center; max-width: 500px;">
                        <div style="font-size: 64px; margin-bottom: 20px;">🔐</div>
                        <h1 style="font-size: 32px; margin-bottom: 10px; font-weight: 300;">Session Expired</h1>
                        <p style="font-size: 16px; color: #ccc; margin-bottom: 30px;">
                            Your session has expired or is invalid. Please log in again to continue.
                        </p>
                        <button onclick="localStorage.removeItem('zoe_session'); window.location.href='${_loginDest}';" 
                                style="background: linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%); 
                                       border: none; color: white; padding: 16px 32px; font-size: 16px; 
                                       border-radius: 12px; cursor: pointer; font-weight: 600;">
                            Go to Login
                        </button>
                    </div>
                `;
                document.body.appendChild(errorDiv);
            };
            
            // Wait for DOM if not ready
            if (document.body) {
                showError();
            } else {
                document.addEventListener('DOMContentLoaded', showError);
            }
        } else {
            if (currentPath.startsWith('/touch/')) {
                // Admin/family users should not get bounced by stale/mismatched
                // capability matrix cache on touch pages.
                if (isAuthenticatedNonGuestSession()) {
                    console.log('✅ Non-guest touch session - skipping matrix page gating');
                    return;
                }
                const cap = await loadCapabilityMatrix();
                const pageId = getPageIdFromPath(currentPath);
                const allowed = !pageId || !cap || !cap.matrix || !cap.matrix.pages
                    ? true
                    : !!cap.matrix.pages[pageId];
                if (!allowed) {
                    try {
                        if (typeof showNotification === 'function') {
                            showNotification('Please sign in with an allowed profile for this page.', 'error');
                        }
                    } catch (_) {}
                    window.location.href = '/touch/dashboard.html';
                    return;
                }
            }
            console.log('✅ Session valid - access granted');
        }
    }

    // Setup fetch interceptor - Install IMMEDIATELY to avoid race conditions
    const originalFetch = window.fetch;
    
    function setupFetchInterceptor() {
        // Idempotent guard - only install once
        if (window.fetch.__zoeInterceptorApplied) {
            console.log('[auth] Interceptor already active, skipping');
            return;
        }
        
        window.fetch = function(url, options = {}) {
            // Initialize options
            options.headers = options.headers || {};
            
            // Add session ID header if available
            const sessionId = getSession();
            if (sessionId) {
                options.headers['X-Session-ID'] = sessionId;
            }
            
            // Extract URL string (handle Request objects)
            let urlString = url;
            if (url instanceof Request) {
                urlString = url.url;
            }
            
            // Remove user_id query parameters (legacy cleanup) and fix malformed URLs
            if (typeof urlString === 'string') {
                // Use same protocol as current page (don't force HTTPS if on HTTP/local)
                // Only convert if we're on HTTPS and URL is HTTP (mixed content prevention)
                if (window.location.protocol === 'https:' && urlString.startsWith('http://')) {
                    urlString = urlString.replace(/^http:\/\//, 'https://');
                    console.log('🔒 Forced HTTP → HTTPS (mixed content prevention):', urlString);
                }
                // If on HTTP/local, keep HTTP (allows self-signed certs to work)
                
                // NOTE: DO NOT remove user_id parameter - it's required for user isolation!
                // Legacy code that was stripping user_id has been removed.
                
                // Clean up any trailing ? or &
                urlString = urlString.replace(/[?&]$/, '');
                
                // Log the FINAL URL that will be used
                if (urlString.startsWith('https://')) {
                    console.log('✅ Final HTTPS URL:', urlString);
                } else if (urlString.startsWith('http://')) {
                    // HTTP is fine if we're on HTTP (local/self-signed certs)
                    if (window.location.protocol === 'http:') {
                        console.log('✅ Final HTTP URL (matches page protocol):', urlString);
                    } else {
                        console.warn('⚠️ HTTP URL on HTTPS page (may cause mixed content):', urlString);
                    }
                } else {
                    console.log('📍 Final relative URL:', urlString);
                }
                
                // Use the cleaned URL
                url = urlString;
            }
            
            // Make request
            return originalFetch(url, options).then(response => {
                if (response.status !== 401) {
                    return response;
                }
                let pathname = '';
                try {
                    const u = typeof urlString === 'string' ? urlString : '';
                    if (u) pathname = new URL(u, window.location.origin).pathname;
                } catch (_e) {}
                const skipClear = /\/api\/auth\/(login|register|password|refresh|guest)/i.test(pathname);
                const allowSessionClear = /\/api\/auth\/(profile|me|session|logout)/i.test(pathname);
                const sidBefore = getSession();
                if (sidBefore && allowSessionClear && !skipClear && !options.__zoe401Retried) {
                    console.warn('⚠️ 401 — clearing stale session and retrying once without X-Session-ID:', url);
                    setSession(null);
                    if (typeof showNotification === 'function') {
                        showNotification('Session expired. Continuing as guest — sign in again for your account.', 'error');
                    }
                    const retryHeaders = { ...(options.headers || {}) };
                    delete retryHeaders['X-Session-ID'];
                    return originalFetch(url, {
                        ...options,
                        headers: retryHeaders,
                        __zoe401Retried: true,
                    });
                }
                console.warn('⚠️ 401 Unauthorized response from:', url);
                return Promise.reject(new Error('Unauthorized'));
            });
        };
        
        // Mark interceptor as installed
        window.fetch.__zoeInterceptorApplied = { 
            appliedAt: Date.now(), 
            original: originalFetch 
        };
        console.log('[auth] ✅ Fetch interceptor installed');
    }

    // Expose auth functions globally
    window.zoeAuth = {
        getSession,
        getSessionObject,
        setSession,
        isAuthenticated,
        getCurrentUser,
        getCurrentSession,
        logout,
        enforceAuth,
        config: AUTH_CONFIG
    };

    // Also expose as ZoeAuth for backwards compatibility
    window.ZoeAuth = window.zoeAuth;

    // CRITICAL: Install fetch interceptor IMMEDIATELY (before any API calls)
    // This prevents race condition where DOMContentLoaded triggers both
    // the interceptor installation AND the first API calls
    setupFetchInterceptor();
    
    // Auth enforcement can wait for DOM (needs UI elements)
    async function populateUserProfile() {
        try {
            const user = await getCurrentUser();
            if (user && (user.user_info?.user_id || user.user_id)) {
                const current = getSessionObject() || {};
                const merged = {
                    ...current,
                    user_id: current.user_id || user.user_id || user.user_info?.user_id,
                    user_info: user.user_info || { user_id: user.user_id }
                };
                setSession(merged);
                console.log('👤 Cached user profile in session');
            }
        } catch (_e) {}
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', async () => {
            await bootstrapTouchGuestSession();
            await enforceAuth();
            await populateUserProfile();
            console.log('✅ Zoe Auth initialized (DOMContentLoaded)');
        });
    } else {
        // DOM already loaded
        (async () => {
            await bootstrapTouchGuestSession();
            await enforceAuth();
            await populateUserProfile();
            console.log('✅ Zoe Auth initialized (immediate)');
        })();
    }
})();
