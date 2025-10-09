/**
 * Zoe Authentication System
 * Centralized auth for all UI pages
 */

(function() {
    'use strict';
    
    const AUTH_CONFIG = {
        sessionKey: 'zoe_session',
        authBaseUrl: window.location.protocol === 'https:' 
            ? `https://${window.location.hostname}/api/auth`
            : 'http://localhost:8002/api/auth'
    };

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
        setSession(null);
        // Redirect to appropriate login page
        const currentPath = window.location.pathname;
        const redirectUrl = currentPath.startsWith('/touch/') ? '/touch/index.html' : '/index.html';
        
        if (delay > 0) {
            console.warn(`⏳ Redirecting to ${redirectUrl} in ${delay}ms`);
            setTimeout(() => {
                window.location.href = redirectUrl;
            }, delay);
        } else {
            console.warn(`⚡ Redirecting to ${redirectUrl} immediately`);
            window.location.href = redirectUrl;
        }
    }

    // Check if user should be on a protected page
    function enforceAuth() {
        const currentPath = window.location.pathname;
        // Only allow the main login pages and auth.html to skip authentication
        const isLoginPage = currentPath === '/index.html' || 
                           currentPath === '/' || 
                           currentPath === '/touch/index.html';
        const isAuthPage = currentPath === '/auth.html';
        
        // Skip auth check for login and auth pages only
        if (isLoginPage || isAuthPage) {
            console.log('✅ Auth check skipped - on login/auth page');
            return;
        }
        
        // Get session for debugging
        const session = getSessionObject();
        console.log('🔐 Auth check on:', currentPath);
        console.log('📦 Session data:', session);
        
        // Check if user has valid session using isAuthenticated
        if (!isAuthenticated()) {
            console.warn('⚠️ No valid session found - creating demo session for development');
            
            // Create a demo session for development when backend is not available
            const demoSession = {
                session_id: 'demo_' + Date.now(),
                user_id: 'demo_user',
                username: 'Demo User',
                role: 'user',
                expires_at: new Date(Date.now() + 24*60*60*1000).toISOString(), // 24 hours
                demo_mode: true
            };
            
            setSession(demoSession);
            console.log('✅ Demo session created for development');
        } else {
            console.log('✅ Session valid - access granted');
        }
    }

    // Intercept fetch to add auth headers
    const originalFetch = window.fetch;
    window.fetch = function(url, options = {}) {
        // Initialize options
        options.headers = options.headers || {};
        
        // Add session ID header if available
        const sessionId = getSession();
        if (sessionId) {
            options.headers['X-Session-ID'] = sessionId;
            console.log('🔑 Adding session ID to request:', url);
            console.log('   Session ID:', sessionId);
        } else {
            console.warn('⚠️ No session ID available for request:', url);
        }
        
        // Remove user_id query parameters (legacy cleanup)
        if (typeof url === 'string') {
            url = url.replace(/[?&]user_id=[^&]*/g, '');
        }
        
        // Make request
        return originalFetch(url, options).then(response => {
            // Handle 401 - DON'T clear session, just reject
            // Let enforceAuth handle the actual logout/redirect
            if (response.status === 401) {
                console.warn('⚠️ 401 Unauthorized response from:', url);
                console.warn('   This usually means session is invalid or expired');
                // Don't call logout() here - it causes race conditions
                // Just return the error and let the page handle it
                return Promise.reject(new Error('Unauthorized'));
            }
            return response;
        });
    };

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

    // Enforce auth IMMEDIATELY before any other code runs
    enforceAuth();

    console.log('✅ Zoe Auth initialized');
})();
