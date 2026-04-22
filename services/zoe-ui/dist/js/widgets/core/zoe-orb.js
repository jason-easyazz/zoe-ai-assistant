/**
 * Zoe Orb Widget
 * Interactive AI assistant with voice recognition and chat
 * Version: 1.0.0
 */

class ZoeOrbWidget extends WidgetModule {
    constructor() {
        super('zoe-orb', {
            version: '1.0.0',
            defaultSize: 'size-large',
            updateInterval: null,
            capabilities: ['voice', 'chat', 'tts']
        });
        
        this.chatOpen = false;
        this.isListening = false;
        this.conversationHistory = [];
        this.currentUtterance = null;
        this.currentAudio = null;
        this._recognition = null;
    }
    
    getTemplate() {
        return `
            <div class="zoe-orb-container" style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; min-height: 200px;">
                <div class="zoe-orb" id="zoeOrb-${this.type}" onclick="window.zoeWidgets?.get('${this.type}')?.startConversation()" style="cursor: pointer;">
                    <div class="zoe-status" id="zoeStatus-${this.type}">Tap to speak</div>
                </div>
            </div>
            
            <!-- Inline Chat Window -->
            <div class="zoe-chat-overlay" id="zoeChat-${this.type}" style="display: none; position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(255,255,255,0.98); backdrop-filter: blur(20px); border-radius: inherit; z-index: 10; flex-direction: column;">
                <div class="zoe-chat-header" style="padding: 12px; background: linear-gradient(135deg, #7B61FF 0%, #8B5CF6 100%); color: white; display: flex; justify-content: space-between; align-items: center; border-radius: inherit; border-bottom-left-radius: 0; border-bottom-right-radius: 0;">
                    <div class="zoe-chat-title" style="font-weight: 500; font-size: 14px;">Chat with Zoe</div>
                    <button class="zoe-chat-close" onclick="window.zoeWidgets?.get('${this.type}')?.closeChat()" style="background: rgba(255,255,255,0.1); border: none; color: white; cursor: pointer; padding: 4px 8px; border-radius: 50%; width: 24px; height: 24px;">×</button>
                </div>
                <div class="zoe-chat-messages" id="zoeMessages-${this.type}" style="flex: 1; padding: 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px;">
                    <div class="zoe-message zoe" style="display: flex; align-items: flex-start; gap: 10px;">
                        <div class="zoe-message-avatar" style="width: 32px; height: 32px; border-radius: 50%; background: linear-gradient(135deg, #4ecdc4 0%, #44a08d 100%); color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 14px;">Z</div>
                        <div class="zoe-message-bubble" style="padding: 12px 16px; border-radius: 18px; font-size: 13px; background: rgba(248, 250, 252, 0.9); color: #334155; border: 1px solid rgba(226, 232, 240, 0.6); border-bottom-left-radius: 4px;">
                            Hi! I'm Zoe, your AI assistant. How can I help you today?
                        </div>
                    </div>
                </div>
                <div class="zoe-chat-input-area" style="padding: 12px; border-top: 1px solid rgba(226, 232, 240, 0.3); display: flex; gap: 8px; align-items: center; background: rgba(248, 250, 252, 0.5);">
                    <textarea class="zoe-chat-input" id="zoeInput-${this.type}" placeholder="Type a message or use voice..." style="flex: 1; border: 1px solid rgba(226, 232, 240, 0.8); border-radius: 24px; padding: 10px 16px; font-size: 12px; resize: none; min-height: 36px; max-height: 120px; background: rgba(255,255,255,0.95); font-family: inherit;"></textarea>
                    <button class="zoe-voice-btn" onclick="window.zoeWidgets?.get('${this.type}')?.toggleVoice()" style="background: linear-gradient(135deg, #7B61FF 0%, #8B5CF6 100%); border: none; border-radius: 50%; width: 36px; height: 36px; color: white; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 16px;">🎤</button>
                    <button class="zoe-send-btn" onclick="window.zoeWidgets?.get('${this.type}')?.sendMessage()" style="background: linear-gradient(135deg, #7B61FF 0%, #8B5CF6 100%); border: none; border-radius: 50%; width: 36px; height: 36px; color: white; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 16px;">➤</button>
                </div>
            </div>
        `;
    }
    
    init(element) {
        super.init(element);
        element.classList.add('zoe-widget');
        
        // Store reference globally for onclick handlers
        if (!window.zoeWidgets) {
            window.zoeWidgets = new Map();
        }
        window.zoeWidgets.set(this.type, this);

        // Stable session ID for this widget — persists across page loads so the
        // Hermes agent pool entry survives navigation and the user always gets
        // a warm response after the first message.
        this._sessionId = localStorage.getItem('orbWidgetSessionId');
        if (!this._sessionId) {
            this._sessionId = 'orb-widget-' + Math.random().toString(36).slice(2, 10);
            localStorage.setItem('orbWidgetSessionId', this._sessionId);
        }

        // Pre-warming via /api/chat/warm/{sid} was a zoe-core optimization
        // and is not served by zoe-data. Skip the warm-up call entirely so
        // we don't spam the console with 404s on every page load.

        
        // Setup text input Enter key handler
        const input = this.element.querySelector(`#zoeInput-${this.type}`);
        if (input) {
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.sendMessage();
                }
            });
        }
    }

    
    async startConversation() {
        // Open chat if not already open
        if (!this.chatOpen) {
            this.openChat();
            await this.activateVoice();
        } else {
            // If chat is open, close it
            this.closeChat();
        }
    }
    
    async toggleVoice() {
        // Toggle voice without closing chat
        if (this.isListening) {
            console.log('🔇 Stopping voice...');
            this.stopVoice();
        } else {
            console.log('🎤 Starting voice...');
            if (!this.chatOpen) {
                this.openChat();
            }
            await this.activateVoice();
        }
    }
    
    async activateVoice() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            this.addMessage('Voice recognition is not supported in this browser.', 'zoe');
            return;
        }
        this.startWebSpeechVoice(SpeechRecognition);
    }

    startWebSpeechVoice(SpeechRecognition) {
        const recognition = new SpeechRecognition();
        recognition.continuous = false;
        recognition.interimResults = false;
        recognition.lang = 'en-AU';
        this._recognition = recognition;

        const orb = this.element.querySelector(`#zoeOrb-${this.type}`);
        const status = this.element.querySelector(`#zoeStatus-${this.type}`);

        if (orb) orb.classList.add('listening');
        if (status) status.textContent = 'Listening...';
        this.isListening = true;

        recognition.onresult = async (event) => {
            const transcript = event.results[0][0].transcript.trim();
            if (transcript) {
                this.addMessage(transcript, 'user');
                await this.processRequestAndSpeak(transcript);
            }
        };

        recognition.onerror = (event) => {
            if (event.error !== 'no-speech') {
                console.error('Speech recognition error:', event.error);
            }
            this.resetState();
        };

        recognition.onend = () => {
            if (this.chatOpen && this.isListening) {
                try { recognition.start(); } catch (e) { this.resetState(); }
            } else {
                this.resetState();
            }
        };

        recognition.start();
    }
    
    stopVoice() {
        if (this._recognition) {
            this.isListening = false;
            try { this._recognition.abort(); } catch (e) { /* ignore */ }
            this._recognition = null;
        }
        this.isListening = false;
        this.resetState();
    }
    
    openChat() {
        const chatOverlay = this.element.querySelector(`#zoeChat-${this.type}`);
        if (chatOverlay) {
            chatOverlay.style.display = 'flex';
            this.chatOpen = true;
            
            // Focus input
            const input = this.element.querySelector(`#zoeInput-${this.type}`);
            if (input) {
                setTimeout(() => input.focus(), 100);
            }
        }
    }
    
    closeChat() {
        const chatOverlay = this.element.querySelector(`#zoeChat-${this.type}`);
        if (chatOverlay) {
            chatOverlay.style.display = 'none';
            this.chatOpen = false;
        }
        
        // Stop voice and clean up
        this.stopVoice();
        this.stopSpeaking();
    }
    
    resetState() {
        const orb = this.element.querySelector(`#zoeOrb-${this.type}`);
        const status = this.element.querySelector(`#zoeStatus-${this.type}`);
        
        if (orb) orb.classList.remove('listening', 'processing');
        if (status) status.textContent = 'Tap to speak';
        this.isListening = false;
    }
    
    async sendMessage() {
        const input = this.element.querySelector(`#zoeInput-${this.type}`);
        if (!input) return;
        
        const message = input.value.trim();
        if (!message) return;
        
        this.addMessage(message, 'user');
        input.value = '';
        
        await this.processRequest(message);
    }
    
    async processRequestAndSpeak(transcript) {
        // Process request AND speak response with Zoe's real voice!
        const orb = this.element.querySelector(`#zoeOrb-${this.type}`);
        if (orb) orb.classList.add('processing');
        
        this.showTypingIndicator();
        
        try {
            const session = window.zoeAuth?.getCurrentSession();
            
            // Step 1: Send text to Zoe's brain
            console.log('🎯 Sending to Zoe:', transcript);
            const chatResponse = await fetch('/api/chat/?stream=false', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Session-ID': session?.session_id || ''
                },
                body: JSON.stringify({
                    message: transcript,
                    session_id: this._sessionId,
                    mode: 'widget_chat'
                })
            });
            
            if (!chatResponse.ok) {
                throw new Error(`HTTP ${chatResponse.status}`);
            }
            
            const data = await chatResponse.json();
            const zoeResponse = data.response || 'I understand.';
            
            this.hideTypingIndicator();
            this.addMessage(zoeResponse, 'zoe');
            
            // Step 2: Speak with Zoe's REAL voice! 🎤
            await this.speakWithZoeVoice(zoeResponse);
            
        } catch (error) {
            console.error('❌ Request failed:', error);
            this.hideTypingIndicator();
            this.addMessage("I'm having trouble right now. Please try again.", 'zoe');
        }
        
        if (orb) orb.classList.remove('processing');
    }
    
    async speakWithZoeVoice(text) {
        this.speakText(text);
    }
    
    async processRequest(transcript) {
        const orb = this.element.querySelector(`#zoeOrb-${this.type}`);
        if (orb) orb.classList.add('processing');
        
        this.showTypingIndicator();
        
        try {
            const session = window.zoeAuth?.getCurrentSession();
            const headers = { 'Content-Type': 'application/json' };
            if (session?.session_id) headers['X-Session-ID'] = session.session_id;
            
            const response = await fetch('/api/chat/?stream=false', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({
                    message: transcript,
                    session_id: this._sessionId,
                    mode: 'widget_chat'
                })
            });
            
            console.log('📡 Response status:', response.status);
            
            if (!response.ok) {
                const errorText = await response.text();
                console.error('❌ Response error:', errorText);
                throw new Error(`HTTP ${response.status}: ${errorText}`);
            }
            
            const data = await response.json();
            
            this.hideTypingIndicator();
            this.addMessage(data.response || 'I understand, but I need more information to help you.', 'zoe');
            
        } catch (error) {
            console.error('Failed to process Zoe request:', error);
            this.hideTypingIndicator();
            
            // Show specific error message or fallback
            let errorMessage = "I'm having trouble connecting right now. ";
            
            if (error.message.includes('401')) {
                errorMessage = "Please refresh the page to reconnect. ";
            } else if (error.message.includes('500')) {
                errorMessage = "I'm experiencing technical difficulties. ";
            } else if (error.message === 'Failed to fetch') {
                errorMessage = "Network connection lost. Please check your internet. ";
            }
            
            errorMessage += "Try again in a moment!";
            this.addMessage(errorMessage, 'zoe');
        }
        
        this.resetState();
    }
    
    addMessage(text, sender) {
        const messages = this.element.querySelector(`#zoeMessages-${this.type}`);
        if (!messages) return;
        
        const messageDiv = document.createElement('div');
        messageDiv.className = `zoe-message ${sender}`;
        messageDiv.style.cssText = 'display: flex; align-items: flex-start; gap: 10px;';
        if (sender === 'user') {
            messageDiv.style.flexDirection = 'row-reverse';
        }
        
        const avatar = document.createElement('div');
        avatar.className = 'zoe-message-avatar';
        avatar.textContent = sender === 'user' ? 'U' : 'Z';
        avatar.style.cssText = `width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 14px; color: white;`;
        avatar.style.background = sender === 'user' ? 
            'linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%)' : 
            'linear-gradient(135deg, #4ecdc4 0%, #44a08d 100%)';
        
        const bubble = document.createElement('div');
        bubble.className = 'zoe-message-bubble';
        bubble.style.cssText = `padding: 12px 16px; border-radius: 18px; font-size: 13px; max-width: 85%; word-wrap: break-word; line-height: 1.4;`;
        
        if (sender === 'user') {
            bubble.style.background = 'linear-gradient(135deg, #7B61FF 0%, #5AE0E0 100%)';
            bubble.style.color = 'white';
            bubble.style.borderBottomRightRadius = '4px';
        } else {
            bubble.style.background = 'rgba(248, 250, 252, 0.9)';
            bubble.style.color = '#334155';
            bubble.style.border = '1px solid rgba(226, 232, 240, 0.6)';
            bubble.style.borderBottomLeftRadius = '4px';
        }
        
        bubble.textContent = text;
        
        messageDiv.appendChild(avatar);
        messageDiv.appendChild(bubble);
        messages.appendChild(messageDiv);
        
        messages.scrollTop = messages.scrollHeight;
        
        this.conversationHistory.push({
            text: text,
            sender: sender,
            timestamp: new Date().toISOString()
        });
        
        // Don't use browser TTS - we use Zoe's real voice via speakWithZoeVoice()
        // Browser TTS is only a fallback if the real TTS fails
        // (TTS is handled by processRequestAndSpeak and speakWithZoeVoice)
    }
    
    showTypingIndicator() {
        const messages = this.element.querySelector(`#zoeMessages-${this.type}`);
        if (!messages) return;
        
        const typingDiv = document.createElement('div');
        typingDiv.className = 'zoe-typing-indicator';
        typingDiv.id = `typing-${this.type}`;
        typingDiv.style.cssText = 'display: flex; align-items: center; gap: 10px; padding: 12px 16px; background: rgba(0, 0, 0, 0.05); border-radius: 18px; border-bottom-left-radius: 4px; max-width: 80px;';
        
        for (let i = 0; i < 3; i++) {
            const dot = document.createElement('div');
            dot.style.cssText = `width: 8px; height: 8px; border-radius: 50%; background: #999; animation: typingDot 1.4s ease-in-out infinite; animation-delay: ${i * 0.2}s;`;
            typingDiv.appendChild(dot);
        }
        
        messages.appendChild(typingDiv);
        messages.scrollTop = messages.scrollHeight;
    }
    
    hideTypingIndicator() {
        const indicator = this.element.querySelector(`#typing-${this.type}`);
        if (indicator) {
            indicator.remove();
        }
    }
    
    async speakText(text) {
        this.stopSpeaking();
        const spokenText = String(text || '').trim();
        if (!spokenText) return;

        // Primary: backend synthesis endpoint.
        try {
            const response = await fetch('/api/voice/synthesize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: spokenText, profile: 'zoe_au_natural_v1' })
            });
            if (!response.ok) throw new Error(`TTS HTTP ${response.status}`);
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            this.currentAudio = new Audio(url);
            this.currentAudio.onended = () => {
                URL.revokeObjectURL(url);
                this.currentAudio = null;
            };
            this.currentAudio.onerror = () => {
                URL.revokeObjectURL(url);
                this.currentAudio = null;
            };
            await this.currentAudio.play();
            return;
        } catch (error) {
            console.warn('Backend TTS unavailable, using browser fallback:', error);
        }

        if (!window.speechSynthesis) {
            console.warn('Text-to-speech not supported');
            return;
        }
        this.currentUtterance = new SpeechSynthesisUtterance(spokenText);
        this.currentUtterance.rate = 0.92;
        this.currentUtterance.pitch = 1.0;
        this.currentUtterance.volume = 0.85;
        const voices = window.speechSynthesis.getVoices();
        const preferredVoice = voices.find((voice) =>
            /female|natasha|samantha|karen|zira|australia|australian/i.test(voice.name)
        );
        if (preferredVoice) this.currentUtterance.voice = preferredVoice;
        this.currentUtterance.onend = () => { this.currentUtterance = null; };
        window.speechSynthesis.speak(this.currentUtterance);
    }
    
    stopSpeaking() {
        if (this.currentAudio) {
            try {
                this.currentAudio.pause();
                this.currentAudio.currentTime = 0;
            } catch (_) {}
            this.currentAudio = null;
        }
        if (window.speechSynthesis && window.speechSynthesis.speaking) {
            window.speechSynthesis.cancel();
            this.currentUtterance = null;
        }
    }
    
    destroy() {
        this.stopVoice();
        this.stopSpeaking();
        if (window.zoeWidgets) {
            window.zoeWidgets.delete(this.type);
        }
        super.destroy();
    }
}

// Expose to global scope for WidgetManager
window.ZoeOrbWidget = ZoeOrbWidget;

// Register widget
if (typeof WidgetRegistry !== 'undefined') {
    WidgetRegistry.register('zoe-orb', new ZoeOrbWidget());
}

// Add typing animation CSS if not already present
if (!document.getElementById('zoe-widget-styles')) {
    const style = document.createElement('style');
    style.id = 'zoe-widget-styles';
    style.textContent = `
        @keyframes typingDot {
            0%, 60%, 100% {
                transform: translateY(0);
                opacity: 0.4;
            }
            30% {
                transform: translateY(-10px);
                opacity: 1;
            }
        }
    `;
    document.head.appendChild(style);
}




