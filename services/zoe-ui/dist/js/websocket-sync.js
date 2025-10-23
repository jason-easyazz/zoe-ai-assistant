/**
 * Zoe WebSocket Synchronization
 * Real-time sync for lists, calendar, and other data across devices
 */

class ZoeWebSocketSync {
    constructor(endpoint, userId) {
        this.endpoint = endpoint;
        this.userId = userId;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.callbacks = {};
        this.reconnectTimeout = null;
        this.pingInterval = null;
        this.isConnected = false;
    }
    
    connect() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            console.log(`✅ WebSocket already connected: ${this.endpoint}`);
            return;
        }
        
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}${this.endpoint}/${this.userId}`;
        
        console.log(`🔌 Connecting WebSocket: ${wsUrl}`);
        
        try {
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                console.log(`✅ WebSocket connected: ${this.endpoint}`);
                this.isConnected = true;
                this.reconnectAttempts = 0;
                
                // Start ping/pong to keep connection alive
                this.startPingInterval();
                
                // Trigger connected callback
                if (this.callbacks['connected']) {
                    this.callbacks['connected']();
                }
            };
            
            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleMessage(data);
                } catch (e) {
                    console.error('Failed to parse WebSocket message:', e);
                }
            };
            
            this.ws.onclose = () => {
                console.log(`❌ WebSocket closed: ${this.endpoint}`);
                this.isConnected = false;
                this.stopPingInterval();
                this.reconnect();
            };
            
            this.ws.onerror = (error) => {
                console.error(`⚠️ WebSocket error: ${this.endpoint}`, error);
                this.isConnected = false;
            };
        } catch (error) {
            console.error(`Failed to create WebSocket: ${error}`);
            this.reconnect();
        }
    }
    
    handleMessage(data) {
        console.log('📥 WebSocket message received:', data);
        const { type } = data;
        
        // Call registered callbacks for this message type
        if (this.callbacks[type]) {
            this.callbacks[type](data);
        }
        
        // Call global message callback
        if (this.callbacks['message']) {
            this.callbacks['message'](data);
        }
    }
    
    on(eventType, callback) {
        this.callbacks[eventType] = callback;
    }
    
    off(eventType) {
        delete this.callbacks[eventType];
    }
    
    send(data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(typeof data === 'string' ? data : JSON.stringify(data));
        } else {
            console.warn('WebSocket not connected, cannot send:', data);
        }
    }
    
    startPingInterval() {
        // Send ping every 30 seconds to keep connection alive
        this.pingInterval = setInterval(() => {
            this.send('ping');
        }, 30000);
    }
    
    stopPingInterval() {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }
    
    reconnect() {
        // Clear any existing reconnect timeout
        if (this.reconnectTimeout) {
            clearTimeout(this.reconnectTimeout);
        }
        
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
            console.log(`🔄 Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
            
            this.reconnectTimeout = setTimeout(() => {
                this.connect();
            }, delay);
        } else {
            console.error(`❌ Max reconnection attempts (${this.maxReconnectAttempts}) reached for ${this.endpoint}`);
            console.log('⚠️ Falling back to polling...');
            this.fallbackToPolling();
        }
    }
    
    fallbackToPolling() {
        // Trigger fallback callback if registered
        if (this.callbacks['fallback']) {
            this.callbacks['fallback']();
        }
    }
    
    disconnect() {
        console.log(`🔌 Disconnecting WebSocket: ${this.endpoint}`);
        this.stopPingInterval();
        if (this.reconnectTimeout) {
            clearTimeout(this.reconnectTimeout);
        }
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.isConnected = false;
    }
}

// Global WebSocket Manager
window.ZoeWebSockets = {
    lists: null,
    calendar: null,
    
    init(userId) {
        console.log('🚀 Initializing Zoe WebSockets for user:', userId);
        
        // Initialize lists WebSocket
        this.lists = new ZoeWebSocketSync('/api/lists/ws', userId);
        this.lists.on('list_created', (data) => {
            console.log('📋 List created:', data);
            // Refresh all list widgets
            if (typeof WidgetManager !== 'undefined' && WidgetManager.updateAll) {
                WidgetManager.updateAll();
            }
        });
        this.lists.on('list_updated', (data) => {
            console.log('📋 List updated:', data);
            // Refresh all list widgets
            if (typeof WidgetManager !== 'undefined' && WidgetManager.updateAll) {
                WidgetManager.updateAll();
            }
        });
        this.lists.on('fallback', () => {
            // Fallback to 5-second polling
            console.log('📋 Lists: Using polling fallback');
            setInterval(() => {
                if (typeof WidgetManager !== 'undefined' && WidgetManager.updateAll) {
                    WidgetManager.updateAll();
                }
            }, 5000);
        });
        this.lists.connect();
        
        // Initialize calendar WebSocket
        this.calendar = new ZoeWebSocketSync('/api/calendar/ws', userId);
        this.calendar.on('event_created', (data) => {
            console.log('📅 Event created:', data);
            // Refresh calendar
            if (typeof loadEvents === 'function') {
                loadEvents();
            }
        });
        this.calendar.on('event_updated', (data) => {
            console.log('📅 Event updated:', data);
            // Refresh calendar
            if (typeof loadEvents === 'function') {
                loadEvents();
            }
        });
        this.calendar.on('fallback', () => {
            // Fallback to 5-second polling
            console.log('📅 Calendar: Using polling fallback');
            setInterval(() => {
                if (typeof loadEvents === 'function') {
                    loadEvents();
                }
            }, 5000);
        });
        this.calendar.connect();
    },
    
    disconnect() {
        if (this.lists) this.lists.disconnect();
        if (this.calendar) this.calendar.disconnect();
    }
};







