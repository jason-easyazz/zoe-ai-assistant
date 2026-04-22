// Inject a tiny stylesheet so the shared refresh button never inherits
// the notifications badge/pulse when it happens to share the
// .notifications-btn class with its neighbour. Kept here so every page
// that loads common.js gets it automatically.
(function injectSharedNavStyles(){
    if (typeof document === 'undefined') return;
    if (document.getElementById('__zoe_shared_nav_styles__')) return;
    const style = document.createElement('style');
    style.id = '__zoe_shared_nav_styles__';
    style.textContent = `
        .refresh-btn.has-notifications { animation: none; }
        .refresh-btn.has-notifications::after { display: none; }
        .refresh-btn::after { display: none !important; }
        .theme-nav-btn { cursor: pointer; }
        .theme-nav-btn.has-notifications { animation: none; }
        .theme-nav-btn.has-notifications::after { display: none !important; }
        .edit-nav-btn.has-notifications { animation: none; }
        .edit-nav-btn.has-notifications::after { display: none !important; }
        /* Bell red-dot + count badge (ported from touch). Only applies to
         * the notifications bell itself, not its refresh/theme/edit siblings. */
        button.notifications-btn.has-notifications:not(.refresh-btn):not(.theme-nav-btn):not(.edit-nav-btn) {
            animation: zoeBellPulse 2s ease-in-out infinite;
            position: relative;
        }
        button.notifications-btn.has-notifications:not(.refresh-btn):not(.theme-nav-btn):not(.edit-nav-btn)::after {
            content: ''; position: absolute; top: 4px; right: 4px;
            width: 10px; height: 10px; background: #ff4757; border-radius: 50%;
            border: 2px solid white;
        }
        .bell-count-badge {
            position: absolute;
            top: -4px;
            right: -4px;
            min-width: 18px;
            height: 18px;
            padding: 0 4px;
            border-radius: 9px;
            background: linear-gradient(135deg, #ff4757 0%, #ff6b7a 100%);
            color: #fff;
            font-size: 11px;
            font-weight: 700;
            line-height: 18px;
            text-align: center;
            box-shadow: 0 2px 6px rgba(255, 71, 87, 0.4);
            border: 2px solid #fff;
            box-sizing: content-box;
            pointer-events: none;
            z-index: 2;
            display: none;
        }
        html.dark-mode .bell-count-badge { border-color: #0d0d1a; }
        @keyframes zoeBellPulse {
            0%, 100% { transform: scale(1); }
            50%      { transform: scale(1.08); }
        }
    `;
    (document.head || document.documentElement).appendChild(style);
})();

// Theme toggle (ported from touch/js/touch-menu.js).
// Three modes: 'light', 'dark', 'auto'. Auto = dark between 7pm and 7am.
// Persisted in localStorage under 'zoe_theme'. The actual dark-mode CSS
// lives in css/dark-mode-shared.css. Pages should include that stylesheet
// + a small early <script> in <head> that calls window.applyZoeTheme() so
// the theme is applied before first paint (prevents light/dark flash).
(function initThemeToggle(){
    if (typeof window === 'undefined') return;
    const THEME_ICONS = { light: '☀️', dark: '🌙', auto: '🌓' };
    const CYCLE = { light: 'dark', dark: 'auto', auto: 'light' };

    function getStored() {
        try { return localStorage.getItem('zoe_theme') || 'auto'; } catch (_) { return 'auto'; }
    }
    function resolveDark(mode) {
        if (mode === 'dark') return true;
        if (mode === 'light') return false;
        const h = new Date().getHours();
        return h < 7 || h >= 19;
    }
    function apply(mode) {
        const dark = resolveDark(mode || getStored());
        document.documentElement.classList.toggle('dark-mode', dark);
        document.body && document.body.classList.remove('dark-mode','light-mode');
        if (dark && document.body) document.body.classList.add('dark-mode');
        const mc = document.querySelector('meta[name="theme-color"]');
        if (mc) mc.content = dark ? '#060610' : '#fafbfc';
    }
    function cycle() {
        const current = getStored();
        const next = CYCLE[current] || 'auto';
        try { localStorage.setItem('zoe_theme', next); } catch (_) {}
        apply(next);
        syncButtons(next);
        try {
            window.dispatchEvent(new CustomEvent('zoe-theme-changed', { detail: { mode: next } }));
        } catch (_) {}
        return next;
    }
    function syncButtons(mode) {
        mode = mode || getStored();
        const icon = THEME_ICONS[mode] || '🔄';
        const label = mode === 'light' ? 'Light mode' : (mode === 'dark' ? 'Dark mode' : 'Auto (day/night)');
        document.querySelectorAll('.theme-nav-btn').forEach(btn => {
            btn.textContent = icon;
            btn.title = `Theme: ${label} (click to change)`;
            btn.dataset.zoeTheme = mode;
        });
    }

    window.applyZoeTheme = apply;
    window.cycleZoeTheme = cycle;
    window.getZoeTheme = getStored;

    function init() {
        apply();
        syncButtons();
        // Re-evaluate auto mode once an hour so 7pm/7am transitions apply
        // without a reload.
        setInterval(() => { if (getStored() === 'auto') apply(); }, 60 * 60 * 1000);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

// API_BASE detection - use relative URLs to leverage nginx proxy
function getApiBase() {
    // Use relative URL - nginx will proxy /api to the correct backend service
    // This works regardless of how the user accesses the site (IP, hostname, external, etc.)
    return '/api';
}

const API_BASE = getApiBase();

// Test API connectivity using relative URL
async function testApiConnectivity() {
    console.log('Testing API connectivity...');
    const testUrl = '/health';
    
    try {
        const response = await fetch(testUrl, { 
            method: 'GET',
            cache: 'no-cache'
        });
        console.log(`API response:`, response.status, response.statusText);
        if (response.ok) {
            console.log(`✅ API is reachable`);
            return '/api';
        }
    } catch (error) {
        console.log(`❌ API failed:`, error.message);
    }
    
    console.log('❌ API not reachable');
    return null;
}

// API health check
async function checkAPI() {
    try {
        const response = await fetch('/health');
        const indicator = document.getElementById('apiStatus');
        if (indicator) {
            if (response.ok) {
                indicator.textContent = 'Online';
                indicator.className = 'api-indicator online';
            } else {
                indicator.textContent = 'Warning';
                indicator.className = 'api-indicator warning';
            }
        }
    } catch (error) {
        console.log('API check failed:', error.message);
        const indicator = document.getElementById('apiStatus');
        if (indicator) {
            indicator.textContent = 'Offline';
            indicator.className = 'api-indicator offline';
        }
    }
}

// More overlay functions
function openMoreOverlay() {
    document.getElementById('moreOverlay').classList.add('active');
}

function closeMoreOverlay() {
    document.getElementById('moreOverlay').classList.remove('active');
}

// Shared refresh button handler. Previously lived only in dashboard.js /
// lists-dashboard.js, which meant nav bars on other pages couldn't use the
// refresh button without a ReferenceError. Exposed globally here so every
// page can opt in.
if (typeof window.forceRefreshCache !== 'function') {
    window.forceRefreshCache = function forceRefreshCache() {
        try {
            if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
                navigator.serviceWorker.controller.postMessage({ type: 'CLEAR_CACHE' });
                setTimeout(() => window.location.reload(true), 300);
                return;
            }
        } catch (_) { /* fall through */ }
        window.location.reload(true);
    };
}

// Shared notifications toggle. Pages that include notifications-panel.js
// will have window.zoeNotifications; otherwise we fall back to a
// per-page openNotifications() if one is defined.
if (typeof window.openNotificationsSafe !== 'function') {
    window.openNotificationsSafe = function openNotificationsSafe() {
        if (window.zoeNotifications && typeof window.zoeNotifications.toggle === 'function') {
            return window.zoeNotifications.toggle();
        }
        if (typeof openNotifications === 'function') {
            return openNotifications();
        }
    };
}

// Touch feedback
function addTouchFeedback(element) {
    element.addEventListener('touchstart', () => {
        element.style.transform = 'scale(0.95)';
    });
    element.addEventListener('touchend', () => {
        element.style.transform = 'scale(1)';
    });
}

// Show notification
function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed; top: 20px; right: 20px; 
        background: ${type === 'error' ? '#ef4444' : type === 'success' ? '#22c55e' : '#7B61FF'};
        color: white; padding: 12px 20px; border-radius: 12px;
        font-size: 14px; font-weight: 500; z-index: 9999;
        box-shadow: 0 8px 24px rgba(0,0,0,0.2);
        max-width: 300px; word-wrap: break-word;
    `;
    notification.textContent = message;
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 3000);
}

// Format time for display
function formatTime(timeStr) {
    if (!timeStr) return '';
    const [hours, minutes] = timeStr.split(':');
    const hour12 = hours % 12 || 12;
    const ampm = hours >= 12 ? 'PM' : 'AM';
    return `${hour12}:${minutes} ${ampm}`;
}

// Format date for display
function formatDate(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

// Update time display.
//
// Pages use two different id conventions for their top nav clock/date:
//   - legacy: #currentTime + #currentDate (long format)
//   - modern: #navCurrentTime + #navCurrentDate (compact format)
// We populate both here so any page that loads common.js gets a live
// clock without duplicating the snippet in every HTML file.
function updateTime() {
    const now = new Date();

    const legacyTime = document.getElementById('currentTime');
    const legacyDate = document.getElementById('currentDate');
    if (legacyTime) {
        legacyTime.textContent = now.toLocaleTimeString([], {
            hour: 'numeric', minute: '2-digit', hour12: true
        });
    }
    if (legacyDate) {
        legacyDate.textContent = now.toLocaleDateString([], {
            weekday: 'long', month: 'long', day: 'numeric'
        });
    }

    const navTime = document.getElementById('navCurrentTime');
    const navDate = document.getElementById('navCurrentDate');
    if (navTime) {
        navTime.textContent = now.toLocaleTimeString([], {
            hour: 'numeric', minute: '2-digit'
        });
    }
    if (navDate) {
        navDate.textContent = now.toLocaleDateString([], {
            weekday: 'short', day: 'numeric', month: 'short'
        });
    }
}

function getServiceMap() {
    const apiBase = getApiBase();
    return {
        '/homeassistant/states': `${apiBase}/homeassistant/entities`,
        '/homeassistant/service': `${apiBase}/homeassistant/services`,
        'default': apiBase
    };
}

// URL normalization helper - Use same protocol as current page
function normalizeToHttps(url) {
    try {
        // If already absolute URL, use as-is
        if (url.startsWith('http://') || url.startsWith('https://')) {
            return url;
        }
        
        // For relative URLs, use same protocol as current page
        const normalized = new URL(url, window.location.origin);
        // Use current page protocol (don't force HTTPS if on HTTP)
        normalized.protocol = window.location.protocol;
        return normalized.toString();
    } catch (e) {
        // If URL construction fails, return as-is (likely already relative)
        console.warn('[common] URL normalization failed for:', url, e);
        return url;
    }
}

// API request helper with microservices support
async function apiRequest(endpoint, options = {}) {
    try {
        // Get dynamic service map
        const SERVICE_MAP = getServiceMap();
        
        // Determine which service to use
        let serviceUrl = SERVICE_MAP['default'];
        let normalizedEndpoint = endpoint;
        
        // Check for exact matches first
        if (SERVICE_MAP[endpoint]) {
            serviceUrl = SERVICE_MAP[endpoint];
            normalizedEndpoint = '';
        } else {
            if (endpoint.startsWith('/api/')) {
                serviceUrl = '';
                normalizedEndpoint = endpoint;
            }
        }
        
        // Ensure endpoint starts with / if it's not empty
        if (normalizedEndpoint && !normalizedEndpoint.startsWith('/')) {
            normalizedEndpoint = `/${normalizedEndpoint}`;
        }
        
        const fullUrl = `${serviceUrl}${normalizedEndpoint}`;
        
        // Convert X-Session-ID headers to Authorization Bearer tokens
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers
        };
        
        // Convert session-based auth to JWT auth
        if (headers['X-Session-ID']) {
            const session = window.zoeAuth?.getCurrentSession();
            if (session && session.token) {
                headers['Authorization'] = `Bearer ${session.token}`;
                delete headers['X-Session-ID'];
            }
        }
        
        const sanitizedUrl = normalizeToHttps(fullUrl);
        const response = await fetch(sanitizedUrl, {
            ...options,
            headers
        });
        
        if (!response.ok) {
            // Try to surface the FastAPI `detail` field from the JSON body
            let detail = `${response.status} ${response.statusText}`;
            try {
                const errBody = await response.clone().json();
                if (errBody && errBody.detail) detail = errBody.detail;
            } catch (_) {}
            throw new Error(detail);
        }

        // Tolerate 204 No Content and empty / non-JSON 200s so that callers
        // (e.g. DELETE handlers) don't throw "Unexpected end of JSON input".
        if (response.status === 204) return null;
        const contentType = (response.headers && response.headers.get)
            ? (response.headers.get('content-type') || '')
            : '';
        const rawText = await response.text();
        if (!rawText) return null;
        if (contentType.includes('application/json')) {
            try { return JSON.parse(rawText); } catch (_) { return rawText; }
        }
        // Fall back: if it happens to be JSON-shaped, parse it; otherwise
        // hand the raw text back so callers can decide what to do.
        try { return JSON.parse(rawText); } catch (_) { return rawText; }
    } catch (error) {
        console.error('API request error:', endpoint, error.message);
        showNotification(`Connection error: ${error.message}`, 'error');
        throw error;
    }
}

// Manual test function for debugging (available in console)
window.testApiConnection = async function() {
    console.log('=== API Connection Test ===');
    console.log('Current location:', window.location.href);
    console.log('Current API_BASE:', API_BASE);
    console.log('Window API_BASE:', window.API_BASE);
    
    const workingApiBase = await testApiConnectivity();
    if (workingApiBase) {
        console.log('✅ Working API base found:', workingApiBase);
        window.API_BASE = workingApiBase;
        console.log('Updated window.API_BASE to:', window.API_BASE);
    } else {
        console.log('❌ No working API base found');
    }
    
    return workingApiBase;
};

// Initialize on all pages
document.addEventListener('DOMContentLoaded', () => {
    checkAPI();
    setInterval(checkAPI, 30000); // Check every 30s
    updateTime();
    setInterval(updateTime, 60000); // Update time every minute
    
    // Add touch feedback to all buttons
    document.querySelectorAll('button, .btn, .touch-target').forEach(addTouchFeedback);
    
    // Close overlays when clicking outside. Pages that don't define a
    // closeMoreOverlay helper (most of them) would otherwise trigger a
    // ReferenceError and break every subsequent click handler.
    document.addEventListener('click', (e) => {
        if (e.target && e.target.classList && e.target.classList.contains('more-overlay')) {
            if (typeof closeMoreOverlay === 'function') {
                closeMoreOverlay();
            } else {
                e.target.classList.remove('active');
            }
        }
    });
});