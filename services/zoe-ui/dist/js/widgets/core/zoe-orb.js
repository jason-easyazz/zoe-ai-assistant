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
        this.recognition = null;
        this.conversationHistory = [];
        this.currentUtterance = null;
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
                    <button class="zoe-voice-btn" onclick="window.zoeWidgets?.get('${this.type}')?.startVoiceRecognition()" style="background: linear-gradient(135deg, #7B61FF 0%, #8B5CF6 100%); border: none; border-radius: 50%; width: 36px; height: 36px; color: white; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 16px;">🎤</button>
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
    
    startConversation() {
        if (this.chatOpen) {
            this.closeChat();
            return;
        }
        
        this.openChat();
        // Auto-start voice recognition
        setTimeout(() => this.startVoiceRecognition(), 300);
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
        
        if (this.isListening) {
            this.stopVoiceRecognition();
        }
        
        this.stopSpeaking();
    }
    
    startVoiceRecognition() {
        if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
            this.addMessage('Sorry, voice recognition is not supported in your browser.', 'zoe');
            return;
        }
        
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        this.recognition = new SpeechRecognition();
        
        this.recognition.continuous = false;
        this.recognition.interimResults = false;
        this.recognition.lang = 'en-US';
        this.recognition.maxAlternatives = 1;
        
        const orb = this.element.querySelector(`#zoeOrb-${this.type}`);
        const status = this.element.querySelector(`#zoeStatus-${this.type}`);
        
        if (orb) orb.classList.add('listening');
        if (status) status.textContent = 'Listening...';
        this.isListening = true;
        
        this.recognition.onstart = () => {
            console.log('🎤 Voice recognition started');
        };
        
        this.recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            console.log('🎤 Recognized:', transcript);
            
            this.addMessage(transcript, 'user');
            this.processRequest(transcript);
        };
        
        this.recognition.onerror = (event) => {
            console.error('🎤 Recognition error:', event.error);
            let errorMessage = 'Sorry, I didn\'t catch that. Please try again.';
            
            switch(event.error) {
                case 'no-speech':
                    errorMessage = 'No speech detected. Please try speaking again.';
                    break;
                case 'audio-capture':
                    errorMessage = 'No microphone found. Please check your microphone.';
                    break;
                case 'not-allowed':
                    errorMessage = 'Microphone access denied. Please allow microphone access.';
                    break;
                case 'network':
                    errorMessage = 'Network error. Please check your connection.';
                    break;
            }
            
            this.addMessage(errorMessage, 'zoe');
            this.resetState();
        };
        
        this.recognition.onend = () => {
            console.log('🎤 Voice recognition ended');
            this.resetState();
        };
        
        try {
            this.recognition.start();
        } catch (error) {
            console.error('Failed to start speech recognition:', error);
            this.addMessage('Failed to start voice recognition. Please try again.', 'zoe');
            this.resetState();
        }
    }
    
    stopVoiceRecognition() {
        if (this.recognition) {
            this.recognition.stop();
        }
        this.resetState();
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
    
    async processRequest(transcript) {
        const orb = this.element.querySelector(`#zoeOrb-${this.type}`);
        if (orb) orb.classList.add('processing');
        
        this.showTypingIndicator();
        
        try {
            const response = await fetch('/api/chat/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: transcript,
                    conversation_history: this.conversationHistory.slice(-10),
                    mode: 'widget_chat'
                })
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            
            this.hideTypingIndicator();
            this.addMessage(data.response || 'I understand, but I need more information to help you.', 'zoe');
            
        } catch (error) {
            console.error('Failed to process Zoe request:', error);
            this.hideTypingIndicator();
            
            const fallbackResponses = [
                "I'm here to help! Could you tell me more about what you need?",
                "That's interesting! How can I assist you with that?",
                "I understand. What would you like me to do for you?",
                "Thanks for sharing! Is there anything specific I can help with?"
            ];
            
            const randomResponse = fallbackResponses[Math.floor(Math.random() * fallbackResponses.length)];
            this.addMessage(randomResponse, 'zoe');
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
        
        if (sender === 'zoe') {
            this.speakText(text);
        }
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
    
    speakText(text) {
        if (!window.speechSynthesis) {
            console.warn('Text-to-speech not supported');
            return;
        }
        
        // Stop any current speech
        if (this.currentUtterance) {
            window.speechSynthesis.cancel();
        }
        
        this.currentUtterance = new SpeechSynthesisUtterance(text);
        this.currentUtterance.rate = 0.9;
        this.currentUtterance.pitch = 1.0;
        this.currentUtterance.volume = 0.8;
        
        // Try to use a female voice
        const voices = window.speechSynthesis.getVoices();
        const femaleVoice = voices.find(voice => 
            voice.name.includes('Female') || 
            voice.name.includes('Samantha') ||
            voice.name.includes('Karen')
        );
        
        if (femaleVoice) {
            this.currentUtterance.voice = femaleVoice;
        }
        
        this.currentUtterance.onend = () => {
            this.currentUtterance = null;
        };
        
        window.speechSynthesis.speak(this.currentUtterance);
    }
    
    stopSpeaking() {
        if (window.speechSynthesis && window.speechSynthesis.speaking) {
            window.speechSynthesis.cancel();
            this.currentUtterance = null;
        }
    }
    
    destroy() {
        this.stopVoiceRecognition();
        this.stopSpeaking();
        
        // Cleanup global reference
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




