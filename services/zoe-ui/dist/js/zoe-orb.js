/**
 * Zoe Orb - Shared Chat Interface
 * Provides consistent orb chat functionality across all pages
 */

// Orb Chat System
let orbChatOpen = false;
let orbChatContext = null; // Store context for deep conversations
let intelligenceWS = null;
let wsRetries = 0;
const MAX_WS_RETRIES = 2;
let orbWebSocket = null;

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
        // Use apiRequest if available, otherwise use fetch
        let response;
        if (typeof apiRequest === 'function') {
            response = await apiRequest('/chat/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: message,
                    context: orbChatContext,
                    mode: 'orb_chat' // Special mode for orb conversations
                })
            });
        } else {
            // Fallback to direct fetch
            const session = window.zoeAuth?.getCurrentSession();
            const apiBase = window.API_BASE || '/api';
            const res = await fetch(`${apiBase}/chat/`, {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    ...(session ? { 'X-Session-ID': session.session_id } : {})
                },
                body: JSON.stringify({
                    message: message,
                    context: orbChatContext,
                    mode: 'orb_chat'
                })
            });
            response = await res.json();
        }
        
        // Remove typing indicator
        hideOrbTyping();
        
        if (response && response.response) {
            addOrbMessage(response.response, 'assistant');
            orbChatContext = response.context || orbChatContext;
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
    
    // Use relative WebSocket URL - nginx will proxy
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/api/ws/intelligence`;
    
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
 * Initialize Intelligence SSE (fallback)
 */
function initIntelligenceSSE() {
    const orb = document.getElementById('zoeOrb');
    if (!orb) return;
    
    // Use relative URL for SSE
    const url = '/api/intelligence/stream';
    
    try {
        const es = new EventSource(url);
        
        es.onopen = () => { 
            orb.classList.remove('error', 'connecting'); 
            orb.classList.add('connected');
            orb.title = 'Connected (SSE)';
            console.log('✅ Intelligence SSE connected');
        };
        
        es.onmessage = (evt) => {
            try { 
                const msg = JSON.parse(evt.data); 
                handleIntelligenceEvent(msg); 
            } catch(e) {
                console.warn('Failed to parse SSE message:', e);
            }
        };
        
        es.onerror = () => { 
            orb.classList.add('error'); 
            orb.title = 'SSE error';
            console.warn('SSE connection error');
        };
    } catch(e) { 
        orb.classList.add('error'); 
        orb.title = 'No realtime channel';
        console.error('Failed to create SSE:', e);
    }
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

// Note: initOrbChat() is called by the page after component loads
// No auto-initialization here to avoid race conditions

