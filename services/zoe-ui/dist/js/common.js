const API_BASE = `${window.location.protocol}//${window.location.host}/api`;

// API health check
async function checkAPI() {
    try {
        const response = await fetch(`${API_BASE}/health`);
        const indicator = document.getElementById('apiStatus');
        if (response.ok) {
            indicator.textContent = 'Online';
            indicator.className = 'api-indicator online';
        } else {
            indicator.textContent = 'Warning';
            indicator.className = 'api-indicator warning';
        }
    } catch (error) {
        document.getElementById('apiStatus').textContent = 'Offline';
        document.getElementById('apiStatus').className = 'api-indicator offline';
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

// API request helper
async function apiRequest(endpoint, options = {}) {
    try {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        });
        
        if (!response.ok) {
            throw new Error(`API request failed: ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('API request error:', error);
        showNotification('Connection error. Please try again.', 'error');
        throw error;
    }
}

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