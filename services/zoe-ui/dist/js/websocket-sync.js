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
        const { type } = data;

        if (type === 'pong' || type === 'connected') return;

        if (this.callbacks[type]) {
            this.callbacks[type](data);
        }

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
    people: null,
    reminders: null,
    notes: null,
    journal: null,

    _refreshListWidget(listType) {
        const widgetNames = {
            'shopping': 'shopping',
            'personal_todos': 'personal',
            'work_todos': 'work',
            'bucket': 'bucket'
        };
        const widgetName = widgetNames[listType] || listType;
        const widget = (typeof WidgetManager !== 'undefined' && WidgetManager.modules)
            ? WidgetManager.modules[widgetName] : null;
        if (widget && typeof widget.update === 'function') {
            widget.update();
        } else if (typeof WidgetManager !== 'undefined' && WidgetManager.updateAll) {
            WidgetManager.updateAll();
        }
    },

    _refreshWidget(name) {
        if (typeof WidgetManager !== 'undefined' && WidgetManager.modules) {
            const widget = WidgetManager.modules[name];
            if (widget && typeof widget.update === 'function') {
                widget.update();
                return;
            }
        }
        if (typeof WidgetManager !== 'undefined' && WidgetManager.updateAll) {
            WidgetManager.updateAll();
        }
    },

    init(userId) {
        // --- Lists ---
        this.lists = new ZoeWebSocketSync('/api/lists/ws', userId);
        ['item_added', 'item_removed', 'item_completed'].forEach(evt => {
            this.lists.on(evt, (data) => {
                this._refreshListWidget(data.list_type || data?.data?.list_type || 'shopping');
            });
        });
        ['list_created', 'list_updated'].forEach(evt => {
            this.lists.on(evt, () => {
                if (typeof WidgetManager !== 'undefined' && WidgetManager.updateAll) WidgetManager.updateAll();
            });
        });
        this.lists.on('fallback', () => {
            setInterval(() => {
                if (typeof WidgetManager !== 'undefined' && WidgetManager.updateAll) WidgetManager.updateAll();
            }, 5000);
        });
        this.lists.connect();

        // --- Calendar ---
        this.calendar = new ZoeWebSocketSync('/api/calendar/ws', userId);
        ['event_created', 'event_updated', 'event_deleted'].forEach(evt => {
            this.calendar.on(evt, () => {
                if (typeof loadEvents === 'function') loadEvents();
                this._refreshWidget('events');
            });
        });
        this.calendar.on('fallback', () => {
            setInterval(() => {
                if (typeof loadEvents === 'function') loadEvents();
            }, 5000);
        });
        this.calendar.connect();

        // --- People ---
        this.people = new ZoeWebSocketSync('/api/people/ws', userId);
        ['people:created', 'people:updated', 'people:deleted'].forEach(evt => {
            this.people.on(evt, () => {
                if (typeof loadPeople === 'function') loadPeople();
            });
        });
        this.people.connect();

        // --- Reminders ---
        this.reminders = new ZoeWebSocketSync('/api/reminders/ws', userId);
        ['reminder_created', 'reminder_updated', 'reminder_deleted',
         'reminder_snoozed', 'reminder_acknowledged'].forEach(evt => {
            this.reminders.on(evt, () => {
                if (typeof loadReminders === 'function') loadReminders();
                this._refreshWidget('reminders');
            });
        });
        this.reminders.connect();

        // --- Notes ---
        this.notes = new ZoeWebSocketSync('/api/notes/ws', userId);
        ['note_created', 'note_updated', 'note_deleted'].forEach(evt => {
            this.notes.on(evt, () => {
                if (typeof loadNotes === 'function') loadNotes();
            });
        });
        this.notes.connect();

        // --- Journal ---
        this.journal = new ZoeWebSocketSync('/api/journal/ws', userId);
        ['entry_created', 'entry_updated', 'entry_deleted'].forEach(evt => {
            this.journal.on(evt, () => {
                if (typeof loadJournalEntriesInline === 'function') loadJournalEntriesInline();
                else if (typeof loadEntries === 'function') loadEntries();
            });
        });
        this.journal.connect();
    },

    disconnect() {
        ['lists', 'calendar', 'people', 'reminders', 'notes', 'journal'].forEach(name => {
            if (this[name]) this[name].disconnect();
        });
    }
};







