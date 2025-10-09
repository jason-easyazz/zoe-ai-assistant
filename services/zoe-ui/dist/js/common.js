// Robust API_BASE detection with fallback
function getApiBase() {
    const protocol = window.location.protocol;
    const host = window.location.host;
    
    // Always use HTTPS for API calls
    let apiBase = `https://${host}/api`;
    
    // If host is zoe.local and we're not on the server, use IP fallback
    if (host === 'zoe.local' && window.location.href.includes('zoe.local')) {
        // Try to detect if we're on the server itself
        const isServer = window.location.href.includes('192.168.1.60') || 
                        window.location.href.includes('localhost') ||
                        window.location.href.includes('127.0.0.1');
        
        if (!isServer) {
            // Use IP address for external access
            apiBase = `https://192.168.1.60/api`;
        }
    }
    
    console.log('Debug API_BASE:', {
        protocol: protocol,
        host: host,
        href: window.location.href,
        apiBase: apiBase,
        isSecure: protocol === 'https:'
    });
    
    return apiBase;
}

const API_BASE = getApiBase();

// Test API connectivity with both protocols
async function testApiConnectivity() {
    const host = window.location.host;
    const protocols = ['https:', 'http:'];
    
    console.log('Testing API connectivity...');
    
    // Try the current host first
    for (const protocol of protocols) {
        const testUrl = `${protocol}//${host}/api/health`;
        console.log(`Testing ${protocol}://${host}/api/health`);
        
        try {
            const response = await fetch(testUrl, { 
                method: 'GET',
                mode: 'cors',
                cache: 'no-cache'
            });
            console.log(`${protocol} response:`, response.status, response.statusText);
            if (response.ok) {
                console.log(`✅ ${protocol} is working!`);
                return testUrl.replace('/health', '');
            }
        } catch (error) {
            console.log(`❌ ${protocol} failed:`, error.message);
        }
    }
    
    // If current host fails, try IP address fallback
    const fallbackHosts = ['192.168.1.60', 'zoe.local'];
    for (const fallbackHost of fallbackHosts) {
        for (const protocol of protocols) {
            const testUrl = `${protocol}//${fallbackHost}/api/health`;
            console.log(`Testing fallback ${protocol}://${fallbackHost}/api/health`);
            
            try {
                const response = await fetch(testUrl, { 
                    method: 'GET',
                    mode: 'cors',
                    cache: 'no-cache'
                });
                console.log(`Fallback ${protocol} response:`, response.status, response.statusText);
                if (response.ok) {
                    console.log(`✅ Fallback ${protocol} is working!`);
                    return testUrl.replace('/health', '');
                }
            } catch (error) {
                console.log(`❌ Fallback ${protocol} failed:`, error.message);
            }
        }
    }
    
    console.log('❌ No working protocol found');
    return null;
}

// API health check with fallback
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
        console.log('Primary API check failed, testing connectivity...');
        
        // Try to find a working protocol
        const workingApiBase = await testApiConnectivity();
        if (workingApiBase) {
            console.log('Found working API base:', workingApiBase);
            // Update the global API_BASE if we found a working one
            window.API_BASE = workingApiBase;
            const indicator = document.getElementById('apiStatus');
            if (indicator) {
                indicator.textContent = 'Online (Fallback)';
                indicator.className = 'api-indicator online';
            }
        } else {
            const indicator = document.getElementById('apiStatus');
            if (indicator) {
                indicator.textContent = 'Offline';
                indicator.className = 'api-indicator offline';
            }
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
        
        // N8N endpoints -> n8n-bridge (direct for now)
        '/n8n/workflows': 'http://localhost:8009/workflows',
        '/n8n/executions': 'http://localhost:8009/executions',
        
        // Default to zoe-core for other endpoints
        'default': apiBase
    };
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
            // Check for specific pattern matches only
            if (endpoint.startsWith('/memories/collections/') && endpoint.includes('/tiles')) {
                serviceUrl = 'http://localhost:8011';
                normalizedEndpoint = endpoint.replace('/memories', '');
            } else if (endpoint.startsWith('/memories/tiles/') && !endpoint.includes('/collections/')) {
                serviceUrl = 'http://localhost:8011';
                normalizedEndpoint = endpoint.replace('/memories', '');
            } else if (endpoint.startsWith('/memories/collections/')) {
                serviceUrl = 'http://localhost:8011';
                normalizedEndpoint = endpoint.replace('/memories', '');
            } else if (endpoint.startsWith('/memories/tiles/')) {
                serviceUrl = 'http://localhost:8011';
                normalizedEndpoint = endpoint.replace('/memories', '');
            }
            // All other endpoints go to zoe-core (default)
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
        
        const response = await fetch(fullUrl, {
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
        console.warn('API request failed, using fallback data:', error.message);
        console.warn('Request endpoint was:', endpoint);
        
        // Return appropriate fallback data based on endpoint
        if (endpoint.includes('/health')) {
            return { status: 'offline', message: 'Backend services not available' };
        } else if (endpoint.includes('/calendar') || endpoint.includes('/events')) {
            return { events: [], message: 'Using demo data - backend offline' };
        } else if (endpoint.includes('/lists') || endpoint.includes('/tasks')) {
            return { lists: [], tasks: [], message: 'Using demo data - backend offline' };
        } else if (endpoint.includes('/memories')) {
            return { memories: [], message: 'Using demo data - backend offline' };
        } else if (endpoint.includes('/notifications')) {
            return { notifications: [], message: 'Using demo data - backend offline' };
        } else {
            return { message: 'Backend services not available', demo_mode: true };
        }
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