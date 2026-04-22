/**
 * Zoe Orb - Shared Chat Interface
 * Provides consistent orb chat functionality across all pages
 */

// Orb Chat System
let orbChatOpen = false;
let orbChatContext = null;
let intelligenceWS = null;
let wsRetries = 0;
const MAX_WS_RETRIES = 2;
let orbWebSocket = null;
let orbSessionId = localStorage.getItem('orbSessionId') || null;

/**
 * Initialize Orb Chat functionality
 * Call this on page load
 */
function initOrbChat() {
    const input = document.getElementById('orbChatInput');
    const sendBtn = document.getElementById('orbChatSend');
    
    if (!input || !sendBtn) {
        console.warn('⚠️ Orb chat elements not ready yet');
        return;
    }
    
    // Auto-resize textarea
    input.addEventListener('input', function() {
        this.style.height = 'auto';
        const newHeight = Math.min(this.scrollHeight, 120);
        this.style.height = newHeight + 'px';
        
        const messagesArea = document.getElementById('orbChatMessages');
        if (messagesArea) {
            const headerHeight = 40;
            const inputHeight = newHeight + 24;
            const availableHeight = Math.max(300, 400 - headerHeight - inputHeight);
            messagesArea.style.maxHeight = availableHeight + 'px';
        }
    });
    
    // Send on Enter (Shift+Enter for new lines)
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendOrbMessage();
        }
    });
    
    console.log('✅ Zoe orb initialized');
    
    // Pre-warm Hermes session for this orb so the first message is fast.
    if (orbSessionId) {
        var session = window.zoeAuth ? window.zoeAuth.getCurrentSession() : null;
        var warmHeaders = { 'Content-Type': 'application/json' };
        if (session && session.session_id) warmHeaders['X-Session-ID'] = session.session_id;
        fetch('/api/chat/warm/' + encodeURIComponent(orbSessionId), {
            method: 'POST', headers: warmHeaders
        }).catch(function() {});
    }

    // Initialize intelligence WebSocket
    initIntelligenceWS();
}

/**
 * Toggle Orb Chat Window
 */
function toggleOrbChat() {
    if (orbChatOpen) {
        closeOrbChat();
    } else {
        openOrbChat();
    }
}

/**
 * Open Orb Chat Window
 */
function openOrbChat() {
    const orb = document.getElementById('zoeOrb');
    const chatWindow = document.getElementById('orbChatWindow');
    const input = document.getElementById('orbChatInput');
    
    if (!orb || !chatWindow || !input) {
        console.warn('Orb chat elements not found');
        return;
    }
    
    orbChatOpen = true;
    orb.classList.add('chatting');
    chatWindow.classList.add('open');
    
    // Reset and focus input after animation
    setTimeout(() => {
        input.style.height = 'auto';
        input.style.height = '36px';
        input.focus();
    }, 300);
    
    // Hide any existing toast
    const toast = document.getElementById('orbToast');
    if (toast) {
        toast.style.display = 'none';
    }
}

/**
 * Close Orb Chat Window
 */
function closeOrbChat() {
    const orb = document.getElementById('zoeOrb');
    const chatWindow = document.getElementById('orbChatWindow');
    
    if (!orb || !chatWindow) return;
    
    orbChatOpen = false;
    orb.classList.remove('chatting');
    chatWindow.classList.remove('open');
}

/**
 * Send message from Orb Chat
 */
async function sendOrbMessage() {
    const input = document.getElementById('orbChatInput');
    const messages = document.getElementById('orbChatMessages');
    const sendBtn = document.getElementById('orbChatSend');
    
    if (!input || !messages || !sendBtn) return;
    
    const message = input.value.trim();
    if (!message) return;
    
    // Add user message
    addOrbMessage(message, 'user');
    input.value = '';
    
    // Reset input to auto-sizing
    input.style.height = 'auto';
    input.style.height = '36px';
    
    // Reset messages area height
    const messagesArea = document.getElementById('orbChatMessages');
    if (messagesArea) {
        messagesArea.style.maxHeight = '300px';
    }
    
    // Disable send button and show typing
    sendBtn.disabled = true;
    showOrbTyping();
    
    try {
        var currentPage = location.pathname.replace(/\.html$/, '').replace(/^\//, '') || 'dashboard';
        var chatPayload = {
            message: message,
            context: orbChatContext,
            mode: 'orb_chat',
            page_context: { page: currentPage, url: location.pathname }
        };
        if (orbSessionId) chatPayload.session_id = orbSessionId;

        var response;
        if (typeof apiRequest === 'function') {
            response = await apiRequest('/api/chat/?stream=false', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(chatPayload)
            });
        } else {
            var session = window.zoeAuth ? window.zoeAuth.getCurrentSession() : null;
            var res = await fetch('/api/chat/?stream=false', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(session ? { 'X-Session-ID': session.session_id } : {})
                },
                body: JSON.stringify(chatPayload)
            });
            response = await res.json();
        }

        hideOrbTyping();

        if (response && response.session_id) {
            orbSessionId = response.session_id;
            localStorage.setItem('orbSessionId', orbSessionId);
        }

        if (response && response.response) {
            addOrbMessage(response.response, 'assistant');
            orbChatContext = response.context || orbChatContext;
            handleOrbUiCommands(response);
        } else {
            addOrbMessage('Sorry, I had trouble processing that. Could you try again?', 'assistant');
        }
    } catch (error) {
        console.error('Orb chat error:', error);
        hideOrbTyping();
        addOrbMessage('I\'m having connection issues. Please try again in a moment.', 'assistant');
    } finally {
        sendBtn.disabled = false;
    }
}

/**
 * Add message to Orb Chat
 */
function addOrbMessage(text, sender) {
    const messages = document.getElementById('orbChatMessages');
    if (!messages) return;
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `orb-chat-message ${sender}`;
    messageDiv.textContent = text;
    
    messages.appendChild(messageDiv);
    messages.scrollTop = messages.scrollHeight;
}

/**
 * Show typing indicator
 */
function showOrbTyping() {
    const messages = document.getElementById('orbChatMessages');
    if (!messages) return;
    
    const typingDiv = document.createElement('div');
    typingDiv.className = 'orb-chat-typing';
    typingDiv.id = 'orbTypingIndicator';
    typingDiv.innerHTML = `
        Zoe is thinking
        <div class="orb-chat-typing-dots">
            <div class="orb-chat-typing-dot"></div>
            <div class="orb-chat-typing-dot"></div>
            <div class="orb-chat-typing-dot"></div>
        </div>
    `;
    
    messages.appendChild(typingDiv);
    messages.scrollTop = messages.scrollHeight;
}

/**
 * Hide typing indicator
 */
function hideOrbTyping() {
    const typing = document.getElementById('orbTypingIndicator');
    if (typing) {
        typing.remove();
    }
}

/**
 * Initialize Intelligence WebSocket
 */
function initIntelligenceWS() {
    const orb = document.getElementById('zoeOrb');
    if (!orb) return;
    
    // Try WebSocket first, fall back to SSE on failure
    if (wsRetries >= MAX_WS_RETRIES) {
        console.log('WebSocket failed after retries, switching to SSE');
        initIntelligenceSSE();
        return;
    }
    
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws/push?channel=all`;
    
    try {
        intelligenceWS = new WebSocket(wsUrl);
        
        intelligenceWS.onopen = () => {
            wsRetries = 0;
            orb.classList.remove('error', 'connecting');
            orb.classList.add('connected');
            orb.title = 'Connected';
            console.log('✅ Intelligence WebSocket connected');
        };
        
        intelligenceWS.onmessage = (evt) => {
            try { 
                const msg = JSON.parse(evt.data); 
                handleIntelligenceEvent(msg); 
            } catch(e) {
                console.warn('Failed to parse WebSocket message:', e);
            }
        };
        
        intelligenceWS.onerror = (error) => {
            console.warn('WebSocket error:', error);
            wsRetries++;
            orb.classList.remove('connected', 'connecting');
            orb.classList.add('error');
            orb.title = 'Connection error';
        };
        
        intelligenceWS.onclose = () => {
            orb.classList.remove('connected', 'error');
            orb.classList.add('connecting');
            orb.title = 'Reconnecting...';
            
            // Retry with exponential backoff
            const delay = Math.min(1000 * Math.pow(2, wsRetries), 10000);
            setTimeout(() => initIntelligenceWS(), delay);
        };
    } catch (e) {
        console.error('Failed to create WebSocket:', e);
        wsRetries++;
        orb.classList.add('error');
        orb.title = 'WS init failed';
        setTimeout(() => initIntelligenceWS(), 2500);
    }
}

/**
 * Initialize Intelligence Polling (fallback when WebSocket unavailable)
 */
function initIntelligenceSSE() {
    const orb = document.getElementById('zoeOrb');
    if (!orb) return;

    orb.classList.remove('error', 'connecting');
    orb.classList.add('connected');
    orb.title = 'Connected (polling)';

    setInterval(async () => {
        try {
            const res = await fetch('/api/notifications/pending');
            if (res.ok) {
                const items = await res.json();
                if (Array.isArray(items) && items.length > 0) {
                    items.forEach(n => handleIntelligenceEvent({
                        type: 'proactive_suggestion', data: n
                    }));
                }
            }
        } catch(e) { /* silent */ }
    }, 60000);
}

/**
 * Handle Intelligence Events
 */
function handleIntelligenceEvent(event) {
    const orb = document.getElementById('zoeOrb');
    if (!orb) return;
    
    if (event.type === 'status') {
        orb.classList.remove('error');
        return;
    }
    if (event.type === 'heartbeat') return;
    if (event.type === 'memory_update') {
        orb.classList.add('thinking');
        showOrbToast(`Context updated • events: ${event.data?.context?.events ?? 0}`, false, false);
        setTimeout(() => { orb.classList.remove('thinking'); }, 1200);
        return;
    }
    if (event.type === 'ui_action') {
        const action = event.data || {};
        const actionType = action.action_type || 'action';
        orb.classList.add('thinking');
        showOrbToast(`Zoe is working: ${actionType}`, false, false);
        setTimeout(() => { orb.classList.remove('thinking'); }, 1600);
        return;
    }
    if (event.type === 'ui_action_status') {
        const result = event.data || {};
        if (result.status === 'success') {
            showOrbToast('Action completed', false, false);
        } else if (result.status === 'failed' || result.status === 'blocked') {
            showOrbToast(`Action ${result.status}: ${result.error_message || 'check details'}`, false, false);
        }
        return;
    }
    if (event.type === 'proactive_suggestion' || event.type === 'ambient_notification') {
        orb.classList.add('badge', 'proactive');
        const n = event.data;
        window.__lastSuggestion = n;
        const html = `
          <div style="font-weight:600; margin-bottom:6px;">${n.title ? n.title : 'Suggestion'}</div>
          <div style="margin-bottom:10px; color:#374151;">${n.message}</div>
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <button class="btn" style="padding:6px 10px; border-radius:8px; border:1px solid #e5e7eb; background:#10b981; color:white;" onclick="suggestionAction(${n.id}, 'accept')">Yes</button>
            <button class="btn" style="padding:6px 10px; border-radius:8px; border:1px solid #e5e7eb; background:white;" onclick="suggestionAction(${n.id}, 'dismiss')">Not now</button>
            <button class="btn" style="padding:6px 10px; border-radius:8px; border:1px solid #e5e7eb; background:white;" onclick="suggestionAction(${n.id}, 'never')">Don't show again</button>
            <button class="btn" style="padding:6px 10px; border-radius:8px; border:1px solid #e5e7eb; background:#7B61FF; color:white;" onclick="handleSuggestionWithChat(window.__lastSuggestion)">💬 Discuss</button>
          </div>`;
        showOrbToast(html, true, true); // Persistent until dismissed
        
        // Also refresh panel list if available
        if (typeof loadNotificationsFromCore === 'function') {
            loadNotificationsFromCore();
        }
    }
}

/**
 * Show Orb Toast Notification
 */
function showOrbToast(text, isHtml = false, persistent = false) {
    const el = document.getElementById('orbToast');
    if (!el) return;
    
    if (isHtml) { 
        el.innerHTML = text; 
    } else { 
        el.textContent = text; 
    }
    el.style.display = 'block';
    clearTimeout(window.__orbToastTimer);
    
    // Only auto-dismiss if not persistent
    if (!persistent) {
        window.__orbToastTimer = setTimeout(() => { 
            el.style.display = 'none'; 
        }, 6000);
    }
}

/**
 * Handle Suggestion with Chat
 */
function handleSuggestionWithChat(suggestion) {
    // Set suggestion context
    orbChatContext = {
        suggestion: suggestion,
        discussion_mode: true
    };
    
    // Add suggestion message to chat
    const messages = document.getElementById('orbChatMessages');
    if (messages) {
        const suggestionDiv = document.createElement('div');
        suggestionDiv.className = 'orb-chat-message assistant';
        suggestionDiv.innerHTML = `
            <strong>💡 Suggestion:</strong><br/>
            ${suggestion.title}<br/><br/>
            ${suggestion.message}<br/><br/>
            <em>Would you like to discuss this further?</em>
        `;
        messages.appendChild(suggestionDiv);
        messages.scrollTop = messages.scrollHeight;
    }
    
    // Open chat and focus input
    if (!orbChatOpen) {
        openOrbChat();
    }
}

/**
 * Handle Suggestion Action
 */
async function suggestionAction(notificationId, action) {
    try {
        // If accepted, create a reminder from suggestion metadata
        if (action === 'accept') {
            const sug = window.__lastSuggestion || {};
            const meta = sug.metadata || {};
            const personName = (sug.title || '').replace('Reconnect Opportunity', '').trim() || (sug.message || '').match(/talked to (.+?) in/i)?.[1] || 'contact';
            const now = new Date();
            const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000);
            const dueDate = tomorrow.toISOString().slice(0, 10);
            const body = {
                title: `Reconnect with ${personName}`,
                description: sug.message || 'Reconnect reminder',
                reminder_type: 'once',
                category: 'personal',
                priority: 'medium',
                due_date: dueDate,
                due_time: '09:00:00'
            };
            
            // Use apiRequest if available
            if (typeof apiRequest === 'function') {
                const res = await apiRequest('/reminders', { 
                    method: 'POST', 
                    headers: { 'Content-Type': 'application/json' }, 
                    body: JSON.stringify(body) 
                });
                if (res && (res.reminder_id || res.reminder || res.message)) {
                    showOrbToast('Reminder added for tomorrow 9:00am', false, false);
                }
            } else {
                const apiBase = window.API_BASE || '/api';
                const res = await fetch(`${apiBase}/reminders`, { 
                    method: 'POST', 
                    headers: { 'Content-Type': 'application/json' }, 
                    body: JSON.stringify(body) 
                });
                if (res.ok) {
                    showOrbToast('Reminder added for tomorrow 9:00am', false, false);
                }
            }
        }

        await fetch(`/api/notifications/${notificationId}/interaction?action=${encodeURIComponent(action)}`, { method: 'POST' });
        const el = document.getElementById('orbToast');
        if (el) {
            el.style.display = 'none';
        }
        
        // Refresh notifications list if available
        if (typeof loadNotificationsFromCore === 'function') {
            await loadNotificationsFromCore();
        }
    } catch (e) { 
        console.warn('Suggestion action failed', e); 
    }
}

/**
 * Handle UI commands from orb chat responses (navigate, notify, etc.)
 */
function handleOrbUiCommands(response) {
    if (!response) return;
    var session = window.zoeAuth ? window.zoeAuth.getCurrentSession() : null;
    var headers = { 'Content-Type': 'application/json' };
    if (session && session.session_id) headers['X-Session-ID'] = session.session_id;
    var localHandlers = [];

    if (response.ui_commands) {
        response.ui_commands.forEach(function(cmd) {
            var actionType = null;
            if (cmd.command === 'navigate') actionType = 'navigate';
            else if (cmd.command === 'notify') actionType = 'notify';
            else if (cmd.command === 'refresh_data') actionType = 'refresh';
            else actionType = 'highlight';

            fetch('/api/ui/actions', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({
                    action_type: actionType,
                    payload: cmd.params || {},
                    requested_by: 'orb',
                    session_id: orbSessionId || null
                })
            }).catch(function() {
                // Fallback to local execution if orchestration endpoint is unavailable.
                localHandlers.push(cmd);
            });
        });
    }

    if (localHandlers.length > 0) {
        localHandlers.forEach(function(cmd) {
            if (cmd.command === 'navigate' && cmd.params && cmd.params.page) {
                showOrbToast('Navigating to ' + cmd.params.page + '...');
                setTimeout(function() { window.location.href = cmd.params.page; }, 1500);
            } else if (cmd.command === 'notify' && cmd.params) {
                showOrbToast(cmd.params.message || '');
            } else if (cmd.command === 'refresh_data') {
                window.location.reload();
            }
        });
    }
}

/**
 * Web Speech API - Voice Input for Orb
 */
let orbRecognition = null;
let orbIsListening = false;

function initOrbVoice() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        const btn = document.getElementById('orbVoiceBtn');
        if (btn) btn.style.display = 'none';
        return;
    }
    orbRecognition = new SpeechRecognition();
    orbRecognition.continuous = false;
    orbRecognition.interimResults = true;
    orbRecognition.lang = 'en-AU';
    orbRecognition.maxAlternatives = 1;

    orbRecognition.onresult = function(event) {
        const input = document.getElementById('orbChatInput');
        if (!input) return;
        let final = '', interim = '';
        for (let i = 0; i < event.results.length; i++) {
            if (event.results[i].isFinal) {
                final += event.results[i][0].transcript;
            } else {
                interim += event.results[i][0].transcript;
            }
        }
        input.value = final || interim;
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    };

    orbRecognition.onend = function() {
        orbIsListening = false;
        const btn = document.getElementById('orbVoiceBtn');
        if (btn) btn.classList.remove('listening');
        const input = document.getElementById('orbChatInput');
        if (input && input.value.trim()) {
            sendOrbMessage();
        }
    };

    orbRecognition.onerror = function(event) {
        orbIsListening = false;
        const btn = document.getElementById('orbVoiceBtn');
        if (btn) btn.classList.remove('listening');
        if (event.error !== 'no-speech' && event.error !== 'aborted') {
            console.warn('Speech recognition error:', event.error);
        }
    };
}

function toggleOrbVoice() {
    if (!orbRecognition) initOrbVoice();
    if (!orbRecognition) return;

    if (!orbChatOpen) openOrbChat();

    if (orbIsListening) {
        orbRecognition.stop();
        orbIsListening = false;
        const btn = document.getElementById('orbVoiceBtn');
        if (btn) btn.classList.remove('listening');
    } else {
        const input = document.getElementById('orbChatInput');
        if (input) input.value = '';
        orbRecognition.start();
        orbIsListening = true;
        const btn = document.getElementById('orbVoiceBtn');
        if (btn) btn.classList.add('listening');
    }
}

// Note: initOrbChat() is called by the page after component loads
// No auto-initialization here to avoid race conditions

// ══════════════════════════════════════════════════════════════════════
// Canvas Orb — State-Driven Premium Animation
// Driven by window._zoeSetOrbMode(state) from touch-ui-executor.js
// States: 'ambient' | 'listening' | 'thinking' | 'responding'
// ══════════════════════════════════════════════════════════════════════
(function () {
    'use strict';

    let _canvas = null;
    let _ctx = null;
    let _raf = null;
    let _state = 'ambient';
    let _t = 0;
    let _initialized = false;

    // Particle system for idle/ambient state
    const PARTICLE_COUNT = 20;
    const particles = [];

    function initParticles(R) {
        particles.length = 0;
        for (let i = 0; i < PARTICLE_COUNT; i++) {
            const angle = (i / PARTICLE_COUNT) * Math.PI * 2;
            const r = R * 0.72 + (Math.random() - 0.5) * R * 0.12;
            particles.push({
                angle: angle + (Math.random() - 0.5) * 0.3,
                r: r,
                baseR: r,
                size: 1.8 + Math.random() * 2.2,
                speed: 0.003 + Math.random() * 0.004,
                phase: Math.random() * Math.PI * 2,
            });
        }
    }

    function drawAmbient(cx, cy, R, t) {
        const alpha = 0.55 + 0.2 * Math.sin(t * 0.8);
        particles.forEach((p) => {
            p.angle += p.speed;
            const radial = p.baseR + Math.sin(t * 1.2 + p.phase) * R * 0.06;
            const x = cx + Math.cos(p.angle) * radial;
            const y = cy + Math.sin(p.angle) * radial;
            const grd = _ctx.createRadialGradient(x, y, 0, x, y, p.size * 2.5);
            grd.addColorStop(0, `rgba(123, 97, 255, ${alpha})`);
            grd.addColorStop(1, 'rgba(123, 97, 255, 0)');
            _ctx.beginPath();
            _ctx.arc(x, y, p.size * 2.5, 0, Math.PI * 2);
            _ctx.fillStyle = grd;
            _ctx.fill();
        });
    }

    function drawListening(cx, cy, R, t) {
        const rings = 4;
        for (let i = 0; i < rings; i++) {
            const progress = ((t * 0.6 + i / rings) % 1);
            const r = R * (0.35 + progress * 0.85);
            const alpha = (1 - progress) * 0.7;
            _ctx.beginPath();
            _ctx.arc(cx, cy, r, 0, Math.PI * 2);
            _ctx.strokeStyle = `rgba(239, 68, 68, ${alpha})`;
            _ctx.lineWidth = 2.5 * (1 - progress * 0.6);
            _ctx.stroke();
        }
        // Inner warm glow
        const innerGrd = _ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.4);
        innerGrd.addColorStop(0, `rgba(251, 191, 36, ${0.3 + 0.15 * Math.sin(t * 4)})`);
        innerGrd.addColorStop(1, 'rgba(239, 68, 68, 0)');
        _ctx.beginPath();
        _ctx.arc(cx, cy, R * 0.4, 0, Math.PI * 2);
        _ctx.fillStyle = innerGrd;
        _ctx.fill();
    }

    function drawThinking(cx, cy, R, t) {
        const arcs = 5;
        for (let i = 0; i < arcs; i++) {
            const rotOffset = (i / arcs) * Math.PI * 2 + t * 1.4;
            const arcLen = 0.4 + 0.25 * Math.sin(t * 2.1 + i * 1.2);
            const r = R * (0.42 + i * 0.07);
            const alpha = 0.5 + 0.25 * Math.sin(t * 1.8 + i);
            _ctx.beginPath();
            _ctx.arc(cx, cy, r, rotOffset, rotOffset + arcLen);
            _ctx.strokeStyle = `rgba(245, 158, 11, ${alpha})`;
            _ctx.lineWidth = 2.8;
            _ctx.stroke();
        }
        // Pulsing center
        const pulse = R * 0.2 * (0.8 + 0.2 * Math.sin(t * 3.5));
        const cGrd = _ctx.createRadialGradient(cx, cy, 0, cx, cy, pulse);
        cGrd.addColorStop(0, `rgba(251, 191, 36, 0.6)`);
        cGrd.addColorStop(1, 'rgba(245, 158, 11, 0)');
        _ctx.beginPath();
        _ctx.arc(cx, cy, pulse, 0, Math.PI * 2);
        _ctx.fillStyle = cGrd;
        _ctx.fill();
    }

    function drawResponding(cx, cy, R, t) {
        const bars = 12;
        for (let i = 0; i < bars; i++) {
            const angle = (i / bars) * Math.PI * 2 - Math.PI / 2;
            const freq = Math.sin(t * 4 + i * 0.6) * 0.5 + 0.5;
            const innerR = R * 0.38;
            const outerR = innerR + R * (0.12 + 0.32 * freq);
            const alpha = 0.5 + 0.35 * freq;
            const x1 = cx + Math.cos(angle) * innerR;
            const y1 = cy + Math.sin(angle) * innerR;
            const x2 = cx + Math.cos(angle) * outerR;
            const y2 = cy + Math.sin(angle) * outerR;
            _ctx.beginPath();
            _ctx.moveTo(x1, y1);
            _ctx.lineTo(x2, y2);
            _ctx.strokeStyle = `rgba(16, 185, 129, ${alpha})`;
            _ctx.lineWidth = 2.8;
            _ctx.lineCap = 'round';
            _ctx.stroke();
        }
        // Teal center pulse
        const p2 = R * 0.22 * (0.85 + 0.15 * Math.sin(t * 5));
        const g2 = _ctx.createRadialGradient(cx, cy, 0, cx, cy, p2);
        g2.addColorStop(0, 'rgba(6, 182, 212, 0.55)');
        g2.addColorStop(1, 'rgba(16, 185, 129, 0)');
        _ctx.beginPath();
        _ctx.arc(cx, cy, p2, 0, Math.PI * 2);
        _ctx.fillStyle = g2;
        _ctx.fill();
    }

    function frame() {
        _raf = requestAnimationFrame(frame);
        if (!_canvas || !_ctx) return;

        const W = _canvas.width;
        const H = _canvas.height;
        const cx = W / 2;
        const cy = H / 2;
        const R = Math.min(W, H) / 2;

        _ctx.clearRect(0, 0, W, H);
        _ctx.lineCap = 'round';
        _ctx.lineJoin = 'round';

        _t += 0.016;

        switch (_state) {
            case 'listening':  drawListening(cx, cy, R, _t);  break;
            case 'thinking':   drawThinking(cx, cy, R, _t);   break;
            case 'responding': drawResponding(cx, cy, R, _t); break;
            default:           drawAmbient(cx, cy, R, _t);    break;
        }
    }

    function mountCanvas() {
        const orb = document.getElementById('zoeOrb') || document.querySelector('.zoe-orb');
        if (!orb || _canvas) return;

        _canvas = document.createElement('canvas');
        _canvas.style.cssText = [
            'position:absolute', 'inset:0', 'width:100%', 'height:100%',
            'border-radius:50%', 'pointer-events:none',
        ].join(';');
        // Only set position if 'static' — fixed/absolute/relative are already valid containing blocks
        if (window.getComputedStyle(orb).position === 'static') {
            orb.style.position = 'relative';
        }
        orb.style.overflow = 'hidden';
        orb.appendChild(_canvas);

        function resize() {
            const rect = orb.getBoundingClientRect();
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            _canvas.width = rect.width * dpr;
            _canvas.height = rect.height * dpr;
            _canvas.style.width = rect.width + 'px';
            _canvas.style.height = rect.height + 'px';
            _ctx = _canvas.getContext('2d');
            _ctx.scale(dpr, dpr);
            initParticles(rect.width / 2);
        }

        resize();
        new ResizeObserver(resize).observe(orb);

        if (!_raf) frame();
        _initialized = true;
    }

    // Expose: hook called by touch-ui-executor.js → voice state changes
    const _origSetOrbMode = window._zoeSetOrbMode;
    window._zoeSetOrbMode = function (mode) {
        _state = mode || 'ambient';
        if (_origSetOrbMode) _origSetOrbMode(mode);
    };

    // Also handle voice events dispatched by executor's push WS directly.
    document.addEventListener('zoe:voice:wake', function () {
        _state = 'listening';
    });

    // Auto-mount when DOM is ready (works whether orb is in page or loaded via orb-loader.js).
    function tryMount() {
        if (!_initialized) mountCanvas();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', tryMount);
    } else {
        tryMount();
    }
    // Also try after a short delay for pages that inject the orb late.
    setTimeout(tryMount, 800);
})();


// ══════════════════════════════════════════════════════════════════════
// zoe:orb-prompt — fired by chat.html when zoe.ui_orb_prompt arrives.
// Expands the orb, speaks the prompt (if TTS-ready), and auto-activates
// the mic so the user can answer by voice (e.g. "Looks good" or "Try again").
// The verifier skill listens for the transcribed reply via /api/chat.
// ══════════════════════════════════════════════════════════════════════
window.addEventListener('zoe:orb-prompt', function (ev) {
    var detail = (ev && ev.detail) || {};
    try {
        // Ensure orb chat is open so mic input has somewhere to land
        if (typeof openOrbChat === 'function' && !window.orbChatOpen) {
            openOrbChat();
        }
        // Store task_id so the next chat message can carry it back for the
        // verifier's feedback classifier.
        if (detail.task_id) {
            window.__zoeOrbPendingTaskId = detail.task_id;
            try { sessionStorage.setItem('zoe_orb_pending_task', detail.task_id); } catch (_) {}
        }
        // Place prompt text in the input as a placeholder hint
        var input = document.getElementById('orbChatInput');
        if (input && detail.prompt) {
            input.setAttribute('data-orb-prompt', detail.prompt);
        }
        // Auto-activate mic after a beat so TTS doesn't pick up its own voice
        if (detail.auto_mic && typeof toggleOrbVoice === 'function') {
            setTimeout(function () {
                try {
                    if (!window.orbIsListening) toggleOrbVoice();
                } catch (e) {
                    console.warn('[orb-prompt] mic auto-activate failed', e);
                }
            }, 900);
        }
    } catch (e) {
        console.warn('[orb-prompt] handler failed', e);
    }
});
