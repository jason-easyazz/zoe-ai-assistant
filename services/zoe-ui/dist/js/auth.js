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
        console.log('📦 Session data:', session ? '(exists)' : '(none)');
        
        // Check if user has valid session using isAuthenticated
        if (!isAuthenticated()) {
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
                errorDiv.innerHTML = `
                    <div style="text-align: center; max-width: 500px;">
                        <div style="font-size: 64px; margin-bottom: 20px;">🔐</div>
                        <h1 style="font-size: 32px; margin-bottom: 10px; font-weight: 300;">Session Expired</h1>
                        <p style="font-size: 16px; color: #ccc; margin-bottom: 30px;">
                            Your session has expired or is invalid. Please log in again to continue.
                        </p>
                        <button onclick="localStorage.removeItem('zoe_session'); window.location.href='/auth.html';" 
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
            console.log('✅ Session valid - access granted');
        }
    }

    // Setup fetch interceptor - install IMMEDIATELY to catch all requests
    const originalFetch = window.fetch;
    function setupFetchInterceptor() {
        console.log('🔧 Installing fetch interceptor immediately...');
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
                // FORCE HTTPS: Convert any http:// to https:// to prevent mixed content
                if (urlString.startsWith('http://')) {
                    urlString = urlString.replace(/^http:\/\//, 'https://');
                    console.log('🔒 Forced HTTP → HTTPS:', urlString);
                }
                
                // Convert absolute HTTPS URLs to relative (cleaner, protocol-independent)
                if (urlString.startsWith('https://')) {
                    const relativePath = urlString.replace(/^https:\/\/[^/]+/, '');
                    console.log('📍 HTTPS → Relative:', urlString, '→', relativePath);
                    urlString = relativePath;
                }
                
                // Remove user_id parameter, handling different positions
                urlString = urlString.replace(/\?user_id=[^&]*&/, '?');  // ?user_id=xxx& -> ?
                urlString = urlString.replace(/&user_id=[^&]*&/, '&');   // &user_id=xxx& -> &
                urlString = urlString.replace(/\?user_id=[^&]*$/, '');   // ?user_id=xxx (end) -> (empty)
                urlString = urlString.replace(/&user_id=[^&]*$/, '');    // &user_id=xxx (end) -> (empty)
                // Clean up any trailing ? or &
                urlString = urlString.replace(/[?&]$/, '');
                
                // Use the cleaned URL
                url = urlString;
            }
            
            // Make request
            return originalFetch(url, options).then(response => {
                // Handle 401 - just log and reject
                // Let enforceAuth handle the actual logout/redirect
                if (response.status === 401) {
                    console.warn('⚠️ 401 Unauthorized response from:', url);
                    return Promise.reject(new Error('Unauthorized'));
                }
                return response;
            });
        };
        console.log('✅ Fetch interceptor installed successfully');
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

    // Install fetch interceptor IMMEDIATELY to catch all requests
    setupFetchInterceptor();
    
    // Initialize auth AFTER DOM is ready to avoid race conditions
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            enforceAuth();
            console.log('✅ Zoe Auth initialized (DOMContentLoaded)');
        });
    } else {
        // DOM already loaded
        enforceAuth();
        console.log('✅ Zoe Auth initialized (immediate)');
    }
})();
