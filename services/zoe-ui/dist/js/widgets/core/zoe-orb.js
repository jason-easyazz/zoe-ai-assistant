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
        
        // HTTP Voice (remote)
        this.mediaRecorder = null;
        this.mediaStream = null;
        this.audioChunks = [];
        
        // LiveKit Voice (local)
        this.room = null;
        this.liveKitSDKLoaded = false;
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
        // 🎯 ADAPTIVE VOICE SYSTEM: Detect local vs remote access
        const isLocal = this.isLocalAccess();
        
        if (isLocal) {
            console.log('🏠 Local network detected - using LiveKit (offline Whisper STT + Zoe TTS)');
            setTimeout(() => this.startLiveKitVoice(), 300);
        } else {
            console.log('🌐 Remote access detected - using HTTP audio (Whisper via HTTPS + Zoe TTS)');
            setTimeout(() => this.startHTTPVoice(), 300);
        }
    }
    
    stopVoice() {
        // Stop audio recording (HTTP method)
        if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
            this.mediaRecorder.stop();
        }
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }
        
        // Stop LiveKit (local method)
        if (this.room) {
            this.room.disconnect();
            this.room = null;
        }
        
        this.isListening = false;
        this.resetState();
    }
    
    isLocalAccess() {
        const hostname = window.location.hostname;
        // Check if accessing via local IP, localhost, or .local domain
        return hostname.match(/^(localhost|127\.0\.0\.1|192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.|.*\.local)$/);
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
    
    async startHTTPVoice() {
        // HTTP Audio Recording + Whisper STT (works through Cloudflare!)
        try {
            const orb = this.element.querySelector(`#zoeOrb-${this.type}`);
            const status = this.element.querySelector(`#zoeStatus-${this.type}`);
            
            if (status) status.textContent = 'Starting...';
            
            // Request microphone access
            this.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            
            if (orb) orb.classList.add('listening');
            if (status) status.textContent = '🎤 Listening... (speak now)';
            this.isListening = true;
            
            // Create audio chunks array
            this.audioChunks = [];
            
            // Start recording
            this.mediaRecorder = new MediaRecorder(this.mediaStream);
            
            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    this.audioChunks.push(event.data);
                }
            };
            
            this.mediaRecorder.onstop = async () => {
                try {
                    if (status) status.textContent = 'Processing...';
                    if (orb) orb.classList.add('processing');
                    
                    // Create audio blob
                    const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
                    console.log('🎤 Recorded audio:', audioBlob.size, 'bytes');
                    
                    // Send to Whisper for transcription
                    const formData = new FormData();
                    formData.append('file', audioBlob, 'audio.webm');
                    
                    const response = await fetch('/api/whisper/transcribe', {
                        method: 'POST',
                        body: formData
                    });
                    
                    if (!response.ok) {
                        throw new Error('Transcription failed');
                    }
                    
                    const result = await response.json();
                    const transcript = result.text.trim();
                    
                    if (transcript) {
                        console.log('🎤 You said:', transcript);
                        this.addMessage(transcript, 'user');
                        
                        // Send to Zoe and speak response with her real voice!
                        await this.processRequestAndSpeak(transcript);
                    } else {
                        if (status) status.textContent = 'No speech detected';
                        setTimeout(() => this.resetState(), 2000);
                    }
                    
                    if (orb) orb.classList.remove('processing');
                    
                    // Restart recording if still in conversation mode
                    if (this.chatOpen && this.isListening) {
                        setTimeout(() => this.startNextRecording(), 500);
                    }
                    
                } catch (error) {
                    console.error('❌ Transcription error:', error);
                    this.addMessage('Voice transcription failed. Please try again.', 'zoe');
                    this.resetState();
                }
            };
            
            // Record for 5 seconds at a time
            this.mediaRecorder.start();
            console.log('✅ HTTP voice started (Whisper STT + Zoe TTS)');
            
            // Auto-stop after 5 seconds
            setTimeout(() => {
                if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
                    this.mediaRecorder.stop();
                }
            }, 5000);
            
        } catch (error) {
            console.error('❌ Failed to start HTTP voice:', error);
            this.addMessage('Microphone access denied or not available. Please check permissions.', 'zoe');
            this.resetState();
        }
    }
    
    startNextRecording() {
        // Continue recording if chat is still open
        if (this.chatOpen && this.isListening && this.mediaStream) {
            this.audioChunks = [];
            this.mediaRecorder = new MediaRecorder(this.mediaStream);
            
            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    this.audioChunks.push(event.data);
                }
            };
            
            this.mediaRecorder.onstop = async () => {
                // Same handling as above
                const status = this.element.querySelector(`#zoeStatus-${this.type}`);
                const orb = this.element.querySelector(`#zoeOrb-${this.type}`);
                
                try {
                    const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
                    
                    if (audioBlob.size < 1000) {
                        // Too small, probably silence - restart
                        if (this.chatOpen && this.isListening) {
                            setTimeout(() => this.startNextRecording(), 500);
                        }
                        return;
                    }
                    
                    if (status) status.textContent = 'Processing...';
                    
                    const formData = new FormData();
                    formData.append('file', audioBlob, 'audio.webm');
                    
                    const response = await fetch('/api/whisper/transcribe', {
                        method: 'POST',
                        body: formData
                    });
                    
                    const result = await response.json();
                    const transcript = result.text.trim();
                    
                    if (transcript && transcript.length > 3) {
                        console.log('🎤 You said:', transcript);
                        this.addMessage(transcript, 'user');
                        await this.processRequestAndSpeak(transcript);
                    }
                    
                    if (this.chatOpen && this.isListening) {
                        setTimeout(() => this.startNextRecording(), 500);
                    }
                    
                } catch (error) {
                    console.error('❌ Transcription error:', error);
                    if (this.chatOpen && this.isListening) {
                        setTimeout(() => this.startNextRecording(), 500);
                    }
                }
            };
            
            this.mediaRecorder.start();
            setTimeout(() => {
                if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
                    this.mediaRecorder.stop();
                }
            }, 5000);
        }
    }
    
    // ===== LIVEKIT VOICE (LOCAL ACCESS) =====
    
    loadLiveKitSDK() {
        if (window.LivekitClient || this.liveKitSDKLoaded) {
            return Promise.resolve();
        }
        
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = '/lib/livekit/livekit-client.umd.min.js';
            script.onload = () => {
                this.liveKitSDKLoaded = true;
                console.log('✅ LiveKit SDK loaded');
                resolve();
            };
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }
    
    async startLiveKitVoice() {
        try {
            await this.loadLiveKitSDK();
            
            const status = this.element.querySelector(`#zoeStatus-${this.type}`);
            const orb = this.element.querySelector(`#zoeOrb-${this.type}`);
            
            if (status) status.textContent = 'Connecting...';
            if (orb) orb.classList.add('processing');
            
            const session = window.zoeAuth?.getCurrentSession();
            const userId = session?.user_info?.user_id || session?.user_id || 'guest';
            
            console.log('🎙️ Starting LiveKit conversation for:', userId);
            
            const tokenResponse = await fetch('/api/voice/start-conversation', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Session-ID': session?.session_id || ''
                },
                body: JSON.stringify({
                    user_id: userId,
                    voice_profile: 'zoe',
                    room_name: `voice-${userId}-${Date.now()}`
                })
            });
            
            if (!tokenResponse.ok) {
                throw new Error(`Token request failed: ${tokenResponse.status}`);
            }
            
            const tokenData = await tokenResponse.json();
            console.log('✅ Got LiveKit token, connecting...');
            
            this.room = new LivekitClient.Room({
                adaptiveStream: true,
                dynacast: true,
            });
            
            this.setupLiveKitListeners();
            
            await this.room.connect(tokenData.livekit_url, tokenData.token);
            console.log('✅ Connected to LiveKit');
            
            if (status) status.textContent = 'Listening...';
            if (orb) {
                orb.classList.remove('processing');
                orb.classList.add('listening');
            }
            this.isListening = true;
            
            await this.room.localParticipant.setMicrophoneEnabled(true);
            console.log('✅ Microphone enabled - speak now!');
            
        } catch (error) {
            console.error('❌ LiveKit error:', error);
            this.addMessage(`Voice connection failed: ${error.message}`, 'zoe');
            this.resetState();
        }
    }
    
    setupLiveKitListeners() {
        if (!this.room) return;
        
        // Listen for data from voice agent (transcription & responses)
        this.room.on(LivekitClient.RoomEvent.DataReceived, (payload, participant) => {
            const decoder = new TextDecoder();
            const data = JSON.parse(decoder.decode(payload));
            
            console.log('📨 Voice agent data:', data);
            
            if (data.type === 'transcription' && data.text) {
                this.addMessage(data.text, 'user');
            } else if (data.type === 'response' && data.text) {
                this.addMessage(data.text, 'zoe');
                // Speak with Zoe's real voice via TTS!
                this.speakWithZoeVoice(data.text);
            }
        });
        
        // Handle disconnection
        this.room.on(LivekitClient.RoomEvent.Disconnected, () => {
            console.log('🔌 LiveKit disconnected');
            this.resetState();
        });
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
            const chatResponse = await fetch('/api/chat/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Session-ID': session?.session_id || ''
                },
                body: JSON.stringify({
                    message: transcript,
                    conversation_history: this.conversationHistory.slice(-10),
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
        try {
            console.log('🔊 Generating Zoe\'s voice...');
            
            // Call TTS service to generate Zoe's voice
            const ttsResponse = await fetch('/api/tts/synthesize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: text,
                    voice: 'zoe',  // Use Zoe's cloned voice!
                    speed: 1.0
                })
            });
            
            if (!ttsResponse.ok) {
                throw new Error('TTS failed');
            }
            
            // Get audio blob
            const audioBlob = await ttsResponse.blob();
            const audioUrl = URL.createObjectURL(audioBlob);
            
            // Play audio
            const audio = new Audio(audioUrl);
            
            audio.onended = () => {
                URL.revokeObjectURL(audioUrl); // Cleanup
                console.log('✅ Zoe finished speaking');
            };
            
            audio.onerror = (e) => {
                console.error('❌ Audio playback error:', e);
            };
            
            await audio.play();
            console.log('🎤 Zoe is speaking with her real voice!');
            
        } catch (error) {
            console.error('❌ TTS failed:', error);
            // Continue without voice if TTS fails
        }
    }
    
    async processRequest(transcript) {
        const orb = this.element.querySelector(`#zoeOrb-${this.type}`);
        if (orb) orb.classList.add('processing');
        
        this.showTypingIndicator();
        
        try {
            // Get session for authentication
            const session = window.zoeAuth?.getCurrentSession();
            console.log('🎯 Orb widget - session:', session ? 'found' : 'NOT FOUND');
            console.log('🎯 Orb widget - session_id:', session?.session_id);
            
            const headers = {
                'Content-Type': 'application/json'
            };
            
            // Add session ID if available
            if (session?.session_id) {
                headers['X-Session-ID'] = session.session_id;
                console.log('✅ Added session ID to headers');
            } else {
                console.warn('⚠️ No session ID available!');
            }
            
            console.log('🎯 Sending message to /api/chat/:', transcript);
            
            const response = await fetch('/api/chat/', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({
                    message: transcript,
                    conversation_history: this.conversationHistory.slice(-10),
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
        // Stop any active voice sessions
        if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
            this.mediaRecorder.stop();
        }
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }
        if (this.room) {
            this.room.disconnect();
            this.room = null;
        }
        
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




