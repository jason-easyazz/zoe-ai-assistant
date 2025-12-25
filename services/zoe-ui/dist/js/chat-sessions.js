/**
 * Chat Sessions Manager
 * Handles loading, displaying, and managing chat history
 * Includes support for widget and orb chats
 */

class ChatSessionManager {
    constructor() {
        this.currentSessionId = null;
        this.sessions = [];
        this.messageCache = new Map();
    }
    
    /**
     * Load all sessions for the current user
     */
    async loadSessions() {
        try {
            const session = window.zoeAuth?.getCurrentSession();
            const userId = session?.user_info?.user_id || session?.user_id || 'default';
            
            console.log('💬 Loading chat sessions for user:', userId);
            
            // Use window.apiRequest if available (handles HTTPS properly)
            let data;
            if (window.apiRequest) {
                data = await window.apiRequest(`/api/chat/sessions?user_id=${userId}`);
            } else {
                const response = await fetch(`/api/chat/sessions?user_id=${userId}`, {
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Session-ID': session?.session_id || ''
                    }
                });
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                
                data = await response.json();
            }
            
            this.sessions = data.sessions || [];
            
            console.log(`✅ Loaded ${this.sessions.length} sessions`);
            this.renderSessions();
            
            return this.sessions;
        } catch (error) {
            console.error('❌ Failed to load chat sessions:', error);
            this.renderEmptyState();
            return [];
        }
    }
    
    /**
     * Render sessions list in sidebar
     */
    renderSessions() {
        const container = document.getElementById('sessionsList');
        if (!container) return;
        
        if (this.sessions.length === 0) {
            this.renderEmptyState();
            return;
        }
        
        container.innerHTML = this.sessions.map(session => {
            const isActive = session.id === this.currentSessionId;
            const messageCount = session.message_count || 0;
            const timeAgo = this.formatTimeAgo(session.updated_at);
            const title = this.getSessionTitle(session);
            
            return `
                <div class="session-item ${isActive ? 'active' : ''}" 
                     data-session-id="${session.id}"
                     onclick="chatSessionManager.loadSession('${session.id}')">
                    <div class="session-title">${this.escapeHtml(title)}</div>
                    <div class="session-meta">
                        <span>${messageCount} messages</span>
                        <span>${timeAgo}</span>
                    </div>
                </div>
            `;
        }).join('');
    }
    
    /**
     * Render empty state when no sessions exist
     */
    renderEmptyState() {
        const container = document.getElementById('sessionsList');
        if (!container) return;
        
        container.innerHTML = `
            <div style="padding: 20px; text-align: center; color: #94a3b8;">
                <p style="margin-bottom: 10px; font-size: 14px;">No sessions yet</p>
                <p style="font-size: 12px; opacity: 0.8;">Start chatting to create your first session!</p>
                <p style="font-size: 11px; margin-top: 10px; opacity: 0.6;">
                    💬 Chats from the main interface, widget, and orb will all appear here
                </p>
            </div>
        `;
    }
    
    /**
     * Load a specific session and display its messages
     */
    async loadSession(sessionId) {
        try {
            console.log('📖 Loading session:', sessionId);
            
            // Check cache first
            if (this.messageCache.has(sessionId)) {
                console.log('✅ Using cached messages');
                this.displayMessages(this.messageCache.get(sessionId));
                this.currentSessionId = sessionId;
                this.renderSessions(); // Re-render to update active state
                return;
            }
            
            // Use window.apiRequest if available (handles HTTPS properly)
            let data;
            if (window.apiRequest) {
                data = await window.apiRequest(`/api/chat/sessions/${sessionId}/messages`);
            } else {
                const session = window.zoeAuth?.getCurrentSession();
                const response = await fetch(`/api/chat/sessions/${sessionId}/messages`, {
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Session-ID': session?.session_id || ''
                    }
                });
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                
                data = await response.json();
            }
            
            const messages = data.messages || [];
            
            console.log(`✅ Loaded ${messages.length} messages`);
            
            // Cache the messages
            this.messageCache.set(sessionId, messages);
            
            this.displayMessages(messages);
            this.currentSessionId = sessionId;
            this.renderSessions(); // Re-render to update active state
            
        } catch (error) {
            console.error('❌ Failed to load session:', error);
            this.showError('Failed to load session messages');
        }
    }
    
    /**
     * Display messages in the chat area
     */
    displayMessages(messages) {
        const chatContainer = document.getElementById('messagesContainer');
        if (!chatContainer) return;
        
        // Clear existing messages except welcome screen
        const welcomeScreen = chatContainer.querySelector('.welcome-screen');
        chatContainer.innerHTML = '';
        if (welcomeScreen && messages.length === 0) {
            chatContainer.appendChild(welcomeScreen);
            return;
        }
        
        messages.forEach(msg => {
            const messageHtml = this.createMessageHtml(msg);
            chatContainer.insertAdjacentHTML('beforeend', messageHtml);
        });
        
        // Scroll to bottom
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }
    
    /**
     * Create HTML for a single message
     */
    createMessageHtml(msg) {
        const isUser = msg.role === 'user';
        const messageClass = isUser ? 'user-message' : 'assistant-message';
        const avatar = isUser ? 'You' : 'Z';
        const content = this.escapeHtml(msg.content);
        const timestamp = msg.created_at ? this.formatMessageTime(msg.created_at) : '';
        
        // Check metadata for source info
        const metadata = msg.metadata ? (typeof msg.metadata === 'string' ? JSON.parse(msg.metadata) : msg.metadata) : {};
        const source = this.getMessageSource(metadata);
        
        return `
            <div class="message-group ${messageClass}">
                <div class="message-avatar">
                    <div class="avatar">${avatar}</div>
                </div>
                <div class="message-bubble">
                    ${this.formatMessageContent(content)}
                    ${timestamp ? `<div class="message-time">${timestamp}${source}</div>` : ''}
                </div>
            </div>
        `;
    }
    
    /**
     * Format message content (handle markdown, line breaks, etc.)
     */
    formatMessageContent(content) {
        // Convert newlines to <br>
        let formatted = content.replace(/\n/g, '<br>');
        
        // Convert **bold** to <strong>
        formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        
        // Convert bullet points
        formatted = formatted.replace(/^• /gm, '&bull; ');
        
        return `<p>${formatted}</p>`;
    }
    
    /**
     * Get message source indicator (widget, orb, or main chat)
     */
    getMessageSource(metadata) {
        if (!metadata) return '';
        
        const context = metadata.context || {};
        const mode = context.mode || metadata.mode || '';
        
        if (mode === 'widget_chat' || mode.includes('widget')) {
            return ' <span style="opacity: 0.6; font-size: 10px;">• via widget</span>';
        }
        if (mode.includes('orb') || mode.includes('voice')) {
            return ' <span style="opacity: 0.6; font-size: 10px;">• via orb</span>';
        }
        return '';
    }
    
    /**
     * Create a new session
     */
    async createNewSession() {
        try {
            // Use window.apiRequest if available (handles HTTPS properly)
            let data;
            if (window.apiRequest) {
                data = await window.apiRequest('/api/chat/sessions/', {
                    method: 'POST',
                    body: JSON.stringify({
                        title: 'New Chat'
                    })
                });
            } else {
                const session = window.zoeAuth?.getCurrentSession();
                const response = await fetch('/api/chat/sessions/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Session-ID': session?.session_id || ''
                    },
                    body: JSON.stringify({
                        title: 'New Chat'
                    })
                });
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                
                data = await response.json();
            }
            
            console.log('✅ Created new session:', data.session_id);
            
            // Clear current chat
            const chatContainer = document.getElementById('messagesContainer');
            if (chatContainer) {
                chatContainer.innerHTML = '';
                // Show welcome screen
                const welcomeScreen = document.querySelector('.welcome-screen');
                if (welcomeScreen) {
                    chatContainer.appendChild(welcomeScreen.cloneNode(true));
                }
            }
            
            this.currentSessionId = data.session_id;
            await this.loadSessions(); // Reload sessions list
            
        } catch (error) {
            console.error('❌ Failed to create new session:', error);
            this.showError('Failed to create new session');
        }
    }
    
    /**
     * Get session title (uses first message or default)
     */
    getSessionTitle(session) {
        if (session.title && session.title !== 'New Chat' && session.title !== 'Chat Session') {
            return session.title;
        }
        
        // Generate title from first message if available
        const messages = this.messageCache.get(session.id);
        if (messages && messages.length > 0) {
            const firstMessage = messages.find(m => m.role === 'user');
            if (firstMessage) {
                const preview = firstMessage.content.substring(0, 40);
                return preview.length < firstMessage.content.length ? preview + '...' : preview;
            }
        }
        
        // Use timestamp as fallback
        const date = new Date(session.created_at);
        return `Chat ${date.toLocaleDateString()}`;
    }
    
    /**
     * Format time ago string
     */
    formatTimeAgo(timestamp) {
        const date = new Date(timestamp);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMins / 60);
        const diffDays = Math.floor(diffHours / 24);
        
        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        if (diffDays < 7) return `${diffDays}d ago`;
        return date.toLocaleDateString();
    }
    
    /**
     * Format message timestamp
     */
    formatMessageTime(timestamp) {
        const date = new Date(timestamp);
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    
    /**
     * Escape HTML to prevent XSS
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    /**
     * Show error message
     */
    showError(message) {
        console.error('💬 Session Manager Error:', message);
        // Could add toast notification here
    }
    
    /**
     * Refresh sessions list (call after sending a message)
     */
    async refresh() {
        console.log('🔄 Refreshing sessions...');
        await this.loadSessions();
    }
    
    /**
     * Clear message cache for a session
     */
    clearCache(sessionId = null) {
        if (sessionId) {
            this.messageCache.delete(sessionId);
        } else {
            this.messageCache.clear();
        }
    }
}

// Global instance
const chatSessionManager = new ChatSessionManager();

// Don't auto-load sessions - let chat.html handle it to avoid duplicate calls
// The chat.html page has its own loadSessions() function that should be used instead
console.log('💬 Chat Sessions Manager initialized (sessions loaded by chat.html)');

// Expose globally for use in chat.html
window.chatSessionManager = chatSessionManager;



