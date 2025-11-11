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
    const testUrl = '/api/health';
    
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
        const response = await fetch(`${API_BASE}/health`);
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

// Update time display
function updateTime() {
    const now = new Date();
    const timeElement = document.getElementById('currentTime');
    const dateElement = document.getElementById('currentDate');
    
    if (timeElement) {
        timeElement.textContent = now.toLocaleTimeString([], { 
            hour: 'numeric', minute: '2-digit', hour12: true 
        });
    }
    if (dateElement) {
        dateElement.textContent = now.toLocaleDateString([], { 
            weekday: 'long', month: 'long', day: 'numeric' 
        });
    }
}

// Service mapping for microservices architecture
function getServiceMap() {
    const apiBase = getApiBase();
    
    return {
        // People-related endpoints -> proxy through zoe-core (exact matches only)
        '/public-memories/?type=people': `${apiBase}/memories/proxy/people`,
        '/memories/?type=people': `${apiBase}/memories/proxy/people`,
        
        // Collections-related endpoints -> proxy through zoe-core (exact matches only)
        '/memories/collections': `${apiBase}/memories/proxy/collections`,
        '/memories/tiles': `${apiBase}/memories/proxy/collections`,
        
        // Home Assistant endpoints -> ha-bridge (through nginx proxy)
        '/homeassistant/states': `${apiBase}/homeassistant/entities`,
        '/homeassistant/service': `${apiBase}/homeassistant/services`,
        
        // N8N endpoints -> n8n-bridge (through nginx proxy)
        '/n8n/workflows': `${apiBase}/n8n/workflows`,
        '/n8n/executions': `${apiBase}/n8n/executions`,
        
        // Default to zoe-core for other endpoints
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
            // If endpoint already starts with /api, use it as-is (don't prepend serviceUrl)
            if (endpoint.startsWith('/api/')) {
                serviceUrl = '';
                normalizedEndpoint = endpoint;
            } else {
                // All collections and tiles routes go through nginx proxy to collections-service
                // The nginx proxy handles routing /api/memories/collections/* to collections-service
                // All other endpoints go to zoe-core (default)
            }
        }
        
        // Ensure endpoint starts with / if it's not empty
        if (normalizedEndpoint && !normalizedEndpoint.startsWith('/')) {
            normalizedEndpoint = `/${normalizedEndpoint}`;
        }
        
        const fullUrl = `${serviceUrl}${normalizedEndpoint}`;
        console.log('Making API request to:', fullUrl);
        console.log('Original endpoint:', endpoint);
        console.log('Service URL:', serviceUrl);
        console.log('Normalized endpoint:', normalizedEndpoint);
        
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
        
        // CRITICAL: Normalize URL to HTTPS before calling fetch
        // This is a backup to the auth.js interceptor
        const sanitizedUrl = normalizeToHttps(fullUrl);
        console.log('[common] 🔧 Sanitized URL:', sanitizedUrl);
        
        const response = await fetch(sanitizedUrl, {
            ...options,
            headers
        });
        
        console.log('Response status:', response.status);
        console.log('Response URL:', response.url);
        
        if (!response.ok) {
            throw new Error(`API request failed: ${response.status} - ${response.statusText}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('API request error:', error);
        console.error('Request endpoint was:', endpoint);
        console.error('Error details:', {
            name: error.name,
            message: error.message,
            stack: error.stack
        });
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
    
    // Close overlays when clicking outside
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('more-overlay')) {
            closeMoreOverlay();
        }
    });
});