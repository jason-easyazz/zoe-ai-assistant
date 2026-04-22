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
        const configuredMax = Number(window.ZOE_WS_MAX_RECONNECT_ATTEMPTS || 0);
        this.maxReconnectAttempts = Number.isFinite(configuredMax) ? configuredMax : 0; // 0 => unlimited
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
        
        const canRetry = this.maxReconnectAttempts <= 0 || this.reconnectAttempts < this.maxReconnectAttempts;
        if (canRetry) {
            this.reconnectAttempts++;
            const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
            const maxLabel = this.maxReconnectAttempts <= 0 ? '∞' : this.maxReconnectAttempts;
            console.log(`🔄 Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${maxLabel})...`);
            
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
    },

    // ── Panel push channel ──────────────────────────────────────────────────
    // Connects to /ws/push and handles:
    //   ui_action   → instantly execute panel commands (bypasses 2s poll)
    //   voice:*     → drive orb state machine automatically
    //   pin_request → show PIN pad
    push: null,

    initPush(panelId, sessionId) {
        // Connect directly to /ws/push?channel=all (query param, not path segment).
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const pushUrl = `${protocol}//${window.location.host}/ws/push?channel=all`;
        this.push = new ZoeWebSocketSync('/ws/push', 'all');
        // Override the connect method to use the correct URL with query param.
        const originalConnect = this.push.connect.bind(this.push);
        this.push.connect = function() {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) return;
            console.log('[ZoeWS] Connecting push channel:', pushUrl);
            try {
                this.ws = new WebSocket(pushUrl);
                this.ws.onopen = () => {
                    this.isConnected = true;
                    this.reconnectAttempts = 0;
                    this.startPingInterval();
                    if (this.callbacks['connected']) this.callbacks['connected']();
                };
                this.ws.onmessage = (event) => {
                    try { this.handleMessage(JSON.parse(event.data)); } catch(e) {}
                };
                this.ws.onclose = () => {
                    this.isConnected = false;
                    this.stopPingInterval();
                    this.reconnect();
                };
                this.ws.onerror = () => { this.isConnected = false; };
            } catch(e) { this.reconnect(); }
        };

        const unwrapPayload = (msg) => ((msg && typeof msg === 'object' && msg.data && typeof msg.data === 'object')
            ? msg.data
            : (msg || {}));

        // ── Instant panel action delivery ──────────────────────────────────
        this.push.on('ui_action', (data) => {
            const payload = unwrapPayload(data);
            const action = payload.action || payload;
            if (!action || !action.action_type) return;
            // Delegate to touch-ui-executor if available, otherwise ignore
            if (window._zoeExecuteAction && typeof window._zoeExecuteAction === 'function') {
                window._zoeExecuteAction(action);
            }
        });

        // ── Voice state machine ────────────────────────────────────────────
        this.push.on('voice:listening_started', () => {
            if (window._zoeSetOrbMode) window._zoeSetOrbMode('listening');
            if (window._zoeResetAutoHomeTimer) window._zoeResetAutoHomeTimer('voice:listening_started');
        });
        this.push.on('voice:thinking', () => {
            if (window._zoeSetOrbMode) window._zoeSetOrbMode('thinking');
            if (window._zoeResetAutoHomeTimer) window._zoeResetAutoHomeTimer('voice:thinking');
        });
        this.push.on('voice:done', () => {
            if (window._zoeSetOrbMode) window._zoeSetOrbMode('ambient');
            if (window._zoeResetAutoHomeTimer) window._zoeResetAutoHomeTimer('voice:done');
        });
        this.push.on('voice:responding', () => {
            if (window._zoeSetOrbMode) window._zoeSetOrbMode('responding');
            if (window._zoeResetAutoHomeTimer) window._zoeResetAutoHomeTimer('voice:responding');
        });

        // ── PIN challenge ──────────────────────────────────────────────────
        this.push.on('panel_pin_request', (data) => {
            const payload = unwrapPayload(data);
            let currentPanelId = '';
            try {
                const params = new URLSearchParams(window.location.search || '');
                currentPanelId = (params.get('panel_id') || localStorage.getItem('zoe_touch_panel_id') || '').trim();
            } catch (_) {}
            if (payload.panel_id && currentPanelId && payload.panel_id !== currentPanelId) {
                return;
            }
            // Prefer modern panel_request_auth flow (touch login page) over
            // the legacy in-page PIN pad UI.
            if (window._zoeExecuteAction && typeof window._zoeExecuteAction === 'function') {
                const challengeId = String(payload.challenge_id || '').trim() || String(Date.now());
                window._zoeExecuteAction({
                    id: `push_pin_${challengeId}`,
                    action_type: 'panel_request_auth',
                    payload: {
                        challenge_id: payload.challenge_id,
                        action_context: payload.action_context || payload.reason || 'Enter PIN',
                        panel_id: payload.panel_id,
                    },
                });
            }
        });

        this.push.connect();
        console.log('[ZoeWS] Panel push channel connected, panel_id:', panelId);
    }
};







