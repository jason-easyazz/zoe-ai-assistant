// Zoe v3.1 - Standardized API Configuration
// Use this in ALL frontend pages for consistency

const ZOE_API = {
    // Base configuration
    BASE: '/api',
    HEALTH: '/health',  // Special case - not under /api
    
    // Standardized endpoints
    ENDPOINTS: {
        HEALTH: '/health',
        TASKS_TODAY: '/api/tasks/today',
        TASKS: '/api/tasks',
        EVENTS: '/api/calendar/events', 
        SHOPPING: '/api/shopping',
        CHAT: '/api/chat',           // For event creation
        CHAT_ENHANCED: '/api/chat/enhanced', // For people detection
        SETTINGS: '/api/settings'
    },
    
    // Standardized fetch wrapper
    async fetch(endpoint, options = {}) {
        try {
            const response = await fetch(endpoint, {
                headers: {
                    'Content-Type': 'application/json',
                    ...options.headers
                },
                ...options
            });
            
            if (!response.ok) {
                throw new Error(`API call failed: ${response.status}`);
            }
            
            return await response.json();
        } catch (error) {
            console.error(`API Error for ${endpoint}:`, error);
            throw error;
        }
    },
    
    // Standardized connection check
    async checkConnection() {
        try {
            const health = await this.fetch(this.HEALTH);
            return health.status === 'healthy';
        } catch {
            return false;
        }
    }
};
