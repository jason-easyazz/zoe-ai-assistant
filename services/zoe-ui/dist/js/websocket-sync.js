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

    /**
     * Keep the page's data fresh.
     *
     * This USED to open six per-resource WebSockets
     * (/api/{lists,calendar,people,reminders,notes,journal}/ws/{user_id}).
     * They never connected -- not once, for any user: the URL is built as
     * `${endpoint}/${userId}` with no query string, while the server requires
     * session_id from the query string or an X-Session-ID header
     * (main.py:2501), and a browser cannot set headers on a WebSocket. Every
     * socket was closed 1008 Unauthorized immediately.
     *
     * The 'fallback' handlers were meant to catch that and poll, but they were
     * unreachable: maxReconnectAttempts defaults to 0 meaning "unlimited", so
     * canRetry never became false and fallbackToPolling() was dead code. Net
     * effect: ~20 failed handshakes every 10 seconds and NO data-change signal
     * at all -- ask Zoe to add an event by voice and the page stayed stale
     * until a manual reload.
     *
     * So: poll, which is what the estate does (touch/home.html opens zero
     * WebSockets -- 15s clock, 30s HA, 5s music, 600s weather).
     *
     * initPush() below is UNAFFECTED: different socket (/ws/push), it does send
     * session_id, and it works.
     */
    _pollTimer: null,
    _pollMs: 30000,
    _visibilityBound: false,

    _refreshAll() {
        // Only handlers whose page-level function exists actually fire, so this
        // is safe to call from any page that loads this module.
        try {
            if (typeof loadEvents === 'function') loadEvents();
            if (typeof loadPeople === 'function') loadPeople();
            if (typeof loadReminders === 'function') loadReminders();
            if (typeof loadNotes === 'function') loadNotes();
            if (typeof loadJournalEntries === 'function') loadJournalEntries();
            else if (typeof loadEntries === 'function') loadEntries();
            this._refreshWidget('events');
            this._refreshWidget('reminders');
            if (typeof WidgetManager !== 'undefined' && WidgetManager.updateAll) {
                WidgetManager.updateAll();
            }
        } catch (err) {
            console.warn('resource refresh failed:', err);
        }
    },

    init(userId) {
        this.userId = userId;
        if (this._pollTimer) return;   // idempotent: some pages call init() twice

        // Never poll a hidden tab: wasted requests, and on this box they compete
        // with the voice path for the same backend.
        this._pollTimer = setInterval(() => {
            if (!document.hidden) this._refreshAll();
        }, this._pollMs);

        if (!this._visibilityBound) {
            this._visibilityBound = true;
            document.addEventListener('visibilitychange', () => {
                if (!document.hidden) this._refreshAll();
            });
        }
    },

    disconnect() {
        if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
        // init() no longer creates these, but a caller may still hold a handle
        // from a page load that predates this change.
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
        // Connect directly to /ws/push with the session query expected by the
        // authenticated push endpoint. Browser WebSockets cannot send the
        // X-Session-ID header used by fetch(), so the query param is required.
        // Touch panels use their dedicated panel channel so panel-scoped voice
        // card actions are delivered without relying on global broadcasts.
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const params = new URLSearchParams();
        if (panelId) params.set('panel_id', panelId);
        else params.set('channel', 'all');
        if (sessionId) params.set('session_id', sessionId);
        else console.warn('[ZoeWS] initPush called without sessionId; push will be rejected');
        const pushUrl = `${protocol}//${window.location.host}/ws/push?${params.toString()}`;
        this.push = new ZoeWebSocketSync('/ws/push', 'all');
        window.zoePushWs = this.push;
        // Override the connect method to use the correct URL with query param.
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

        this.push.on('background_task_done', () => {
            if (typeof checkPendingTasks === 'function') {
                checkPendingTasks();
            }
        });
        this.push.on('background_task_error', () => {
            if (typeof checkPendingTasks === 'function') {
                checkPendingTasks();
            }
        });

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

        // ── Voice: start conversation signal (sent after "let's chat" intent) ──
        // Forwarded to the voice page via BroadcastChannel so the page can
        // open the mic after the TTS echo has settled, without relying on ?conv=1.
        this.push.on('voice:start_conversation', (data) => {
            const payload = unwrapPayload(data);
            const delayMs = (payload && typeof payload.delay_ms === 'number') ? payload.delay_ms : 2500;
            if (typeof window.handleStartConversation === 'function') {
                window.handleStartConversation(delayMs);
            }
            try {
                const bc = new BroadcastChannel('zoe-voice');
                bc.postMessage({ type: 'voice:start_conversation', delay_ms: delayMs });
                setTimeout(() => bc.close(), 500);
            } catch(_) {}
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
            let knownPanelIds = [];
            let authoritativePanelIds = [];
            try {
                const params = new URLSearchParams(window.location.search || '');
                const urlPanelId = String(params.get('panel_id') || '').trim();
                const registeredPanelId = String(localStorage.getItem('zoe_panel_id') || '').trim();
                const aliasPanelId = String(localStorage.getItem('zoe_touch_panel_id') || '').trim();
                const generatedAliasId = String(localStorage.getItem('zoe_touch_panel_alias_generated') || '').trim();
                knownPanelIds = [
                    urlPanelId,
                    registeredPanelId,
                    aliasPanelId,
                ].filter(Boolean);
                authoritativePanelIds = [
                    (urlPanelId && urlPanelId !== generatedAliasId) ? urlPanelId : '',
                    registeredPanelId,
                ].filter(Boolean);
            } catch (_) {}
            const requestedPanelId = String(payload.panel_id || '').trim();
            // Alias-only browsers can receive a canonical registered-id payload
            // after the server resolves their socket subscription, so only reject
            // mismatches once the browser knows an authoritative id itself.
            if (requestedPanelId && authoritativePanelIds.length && !knownPanelIds.includes(requestedPanelId)) {
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

        // ── Multica board task updates ─────────────────────────────────────
        this.push.on('multica_task_progress', (data) => {
            const payload = unwrapPayload(data);
            const title = payload.title || 'Board task';
            if (payload.status === 'in_review' && payload.pr_url && typeof window._zoeShowNotification === 'function') {
                window._zoeShowNotification(`Board task in review: ${title}`, 'info');
            }
            document.dispatchEvent(new CustomEvent('zoe:multica_task_progress', { detail: payload }));
        });

        this.push.on('multica_task_done', (data) => {
            const payload = unwrapPayload(data);
            const title = payload.title || 'Board task';
            if (typeof window._zoeShowNotification === 'function') {
                window._zoeShowNotification(`Board task done: ${title}`, 'success');
            } else {
                // Fallback: dispatch a custom event that chat.html can listen for
                document.dispatchEvent(new CustomEvent('zoe:multica_task_done', { detail: payload }));
            }
        });

        this.push.connect();
        console.log('[ZoeWS] Panel push channel connected, panel_id:', panelId);
    }
};
