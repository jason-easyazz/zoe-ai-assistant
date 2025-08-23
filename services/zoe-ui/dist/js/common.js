// Common JavaScript for all Zoe pages
// Uses relative URLs - works from any device!

// No need for API_BASE with full URLs anymore
// Just use relative paths starting with /api

// Helper function for API calls
async function apiCall(endpoint, options = {}) {
    // Ensure endpoint starts with /
    if (!endpoint.startsWith('/')) {
        endpoint = '/' + endpoint;
    }
    
    // If it doesn't start with /api, add it
    if (!endpoint.startsWith('/api')) {
        endpoint = '/api' + endpoint;
    }
    
    try {
        const response = await fetch(endpoint, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('API call failed:', error);
        throw error;
    }
}

// WebSocket connection helper (if needed)
function connectWebSocket(path = '/ws') {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}${path}`;
    return new WebSocket(wsUrl);
}

console.log('Zoe API ready - using relative URLs');
