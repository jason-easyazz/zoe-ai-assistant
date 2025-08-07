/**
 * Zoe v3.1 Voice Controls
 * Advanced voice interaction with Whisper STT and Coqui TTS
 */

class ZoeVoiceController {
    constructor() {
        this.isRecording = false;
        this.isPlaying = false;
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.websocket = null;
        
        this.init();
    }
    
    async init() {
        // Check voice service availability
        try {
            const response = await fetch('/api/health');
            const health = await response.json();
            
            if (health.integrations && health.integrations.voice) {
                this.enableVoiceControls();
            } else {
                this.showVoiceUnavailable();
            }
        } catch (error) {
            console.warn('Could not check voice service status:', error);
        }
    }
    
    enableVoiceControls() {
        // Add voice buttons to chat interface
        const inputContainer = document.querySelector('.input-area');
        if (inputContainer && !document.getElementById('voice-controls')) {
            const voiceControls = document.createElement('div');
            voiceControls.id = 'voice-controls';
            voiceControls.innerHTML = `
                <button id="voice-input-btn" class="btn-icon voice-btn" title="Voice input">
                    🎤
                </button>
                <button id="voice-output-btn" class="btn-icon voice-btn" title="Speak last response">
                    🔊
                </button>
            `;
            
            inputContainer.prepend(voiceControls);
            
            // Add event listeners
            document.getElementById('voice-input-btn').addEventListener('click', () => {
                this.toggleRecording();
            });
            
            document.getElementById('voice-output-btn').addEventListener('click', () => {
                this.speakLastResponse();
            });
        }
    }
    
    showVoiceUnavailable() {
        console.info('Voice services not available - running in text-only mode');
    }
    
    async toggleRecording() {
        if (this.isRecording) {
            this.stopRecording();
        } else {
            await this.startRecording();
        }
    }
    
    async startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ 
                audio: {
                    sampleRate: 16000,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true
                } 
            });
            
            this.mediaRecorder = new MediaRecorder(stream);
            this.audioChunks = [];
            
            this.mediaRecorder.ondataavailable = (event) => {
                this.audioChunks.push(event.data);
            };
            
            this.mediaRecorder.onstop = () => {
                this.processRecording();
            };
            
            this.mediaRecorder.start();
            this.isRecording = true;
            
            // Update UI
            const voiceBtn = document.getElementById('voice-input-btn');
            if (voiceBtn) {
                voiceBtn.innerHTML = '⏹️';
                voiceBtn.classList.add('recording');
            }
            
            this.showRecordingIndicator();
            
        } catch (error) {
            console.error('Failed to start recording:', error);
            this.showError('Could not access microphone');
        }
    }
    
    stopRecording() {
        if (this.mediaRecorder && this.isRecording) {
            this.mediaRecorder.stop();
            this.mediaRecorder.stream.getTracks().forEach(track => track.stop());
            this.isRecording = false;
            
            // Update UI
            const voiceBtn = document.getElementById('voice-input-btn');
            if (voiceBtn) {
                voiceBtn.innerHTML = '🎤';
                voiceBtn.classList.remove('recording');
            }
            
            this.hideRecordingIndicator();
        }
    }
    
    async processRecording() {
        const audioBlob = new Blob(this.audioChunks, { type: 'audio/wav' });
        
        try {
            this.showProcessingIndicator();
            
            // Convert to base64
            const arrayBuffer = await audioBlob.arrayBuffer();
            const base64Audio = btoa(String.fromCharCode(...new Uint8Array(arrayBuffer)));
            
            // Send to transcription service
            const response = await fetch('/api/voice/transcribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    audio_data: base64Audio,
                    format: 'wav'
                })
            });
            
            if (!response.ok) {
                throw new Error('Transcription failed');
            }
            
            const result = await response.json();
            
            // Insert transcribed text into chat input
            const messageInput = document.getElementById('chatInput') || document.getElementById('messageInput');
            if (messageInput && result.text) {
                messageInput.value = result.text;
                messageInput.focus();
                
                // Trigger input event to update UI
                messageInput.dispatchEvent(new Event('input'));
                
                // Auto-send if text is detected
                if (result.text.trim().length > 0) {
                    // Give user a moment to review, then send
                    setTimeout(() => {
                        if (window.zoe && typeof window.zoe.sendMessage === 'function') {
                            window.zoe.sendMessage();
                        } else if (typeof sendMessage === 'function') {
                            sendMessage();
                        }
                    }, 1000);
                }
            }
            
        } catch (error) {
            console.error('Voice transcription failed:', error);
            this.showError('Voice transcription failed. Please try again.');
        } finally {
            this.hideProcessingIndicator();
        }
    }
    
    async speakLastResponse() {
        const messages = document.querySelectorAll('.message.assistant');
        if (messages.length === 0) {
            this.showError('No response to speak');
            return;
        }
        
        const lastMessage = messages[messages.length - 1];
        const textElement = lastMessage.querySelector('.message-text');
        if (!textElement) return;
        
        const text = textElement.textContent || textElement.innerText;
        await this.speakText(text);
    }
    
    async speakText(text) {
        if (this.isPlaying) {
            this.stopSpeaking();
            return;
        }
        
        try {
            this.isPlaying = true;
            
            // Update speak button
            const speakBtn = document.getElementById('voice-output-btn');
            if (speakBtn) {
                speakBtn.innerHTML = '⏹️';
                speakBtn.classList.add('speaking');
            }
            
            // Try Zoe's TTS service first
            const response = await fetch('/api/voice/synthesize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: text,
                    speed: 1.0
                })
            });
            
            if (response.ok) {
                const data = await response.json();
                
                if (data.success && data.audio_data) {
                    // Decode base64 audio and play
                    const audioData = atob(data.audio_data);
                    const audioArray = new Uint8Array(audioData.length);
                    for (let i = 0; i < audioData.length; i++) {
                        audioArray[i] = audioData.charCodeAt(i);
                    }
                    
                    const audioBlob = new Blob([audioArray], { type: 'audio/wav' });
                    const audioUrl = URL.createObjectURL(audioBlob);
                    const audio = new Audio(audioUrl);
                    
                    audio.onended = () => {
                        this.stopSpeaking();
                        URL.revokeObjectURL(audioUrl);
                    };
                    
                    await audio.play();
                } else {
                    // Fallback to browser TTS
                    this.speakWithBrowserTTS(text);
                }
            } else {
                // Fallback to browser TTS
                this.speakWithBrowserTTS(text);
            }
            
        } catch (error) {
            console.error('TTS failed:', error);
            // Fallback to browser TTS
            this.speakWithBrowserTTS(text);
        }
    }
    
    speakWithBrowserTTS(text) {
        if ('speechSynthesis' in window) {
            const utterance = new SpeechSynthesisUtterance(text);
            utterance.onend = () => this.stopSpeaking();
            utterance.rate = 0.9;
            utterance.pitch = 1.0;
            speechSynthesis.speak(utterance);
        } else {
            this.showError('Text-to-speech not supported');
            this.stopSpeaking();
        }
    }
    
    stopSpeaking() {
        this.isPlaying = false;
        
        // Stop browser TTS
        if ('speechSynthesis' in window) {
            speechSynthesis.cancel();
        }
        
        // Update speak button
        const speakBtn = document.getElementById('voice-output-btn');
        if (speakBtn) {
            speakBtn.innerHTML = '🔊';
            speakBtn.classList.remove('speaking');
        }
    }
    
    showRecordingIndicator() {
        this.removeIndicators();
        const indicator = document.createElement('div');
        indicator.id = 'voice-indicator';
        indicator.className = 'voice-indicator recording';
        indicator.innerHTML = '🎤 Recording...';
        document.body.appendChild(indicator);
    }
    
    showProcessingIndicator() {
        this.removeIndicators();
        const indicator = document.createElement('div');
        indicator.id = 'voice-indicator';
        indicator.className = 'voice-indicator processing';
        indicator.innerHTML = '🧠 Processing speech...';
        document.body.appendChild(indicator);
    }
    
    hideRecordingIndicator() {
        this.removeIndicators();
    }
    
    hideProcessingIndicator() {
        this.removeIndicators();
    }
    
    removeIndicators() {
        const existing = document.getElementById('voice-indicator');
        if (existing) {
            existing.remove();
        }
    }
    
    showError(message) {
        // Create temporary error notification
        const error = document.createElement('div');
        error.className = 'voice-error';
        error.textContent = message;
        error.style.cssText = `
            position: fixed;
            top: 80px;
            right: 20px;
            background: #ef4444;
            color: white;
            padding: 12px 20px;
            border-radius: 8px;
            z-index: 1000;
            font-weight: 500;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        `;
        
        document.body.appendChild(error);
        
        setTimeout(() => {
            error.remove();
        }, 3000);
    }
}

// Auto-initialize voice controls
document.addEventListener('DOMContentLoaded', () => {
    window.zoeVoice = new ZoeVoiceController();
});

// Add voice control styles
const voiceStyles = document.createElement('style');
voiceStyles.textContent = `
    .voice-btn {
        font-size: 18px !important;
        margin-right: 8px;
        transition: all 0.3s ease;
    }
    
    .voice-btn:hover {
        transform: scale(1.1);
    }
    
    .voice-btn.recording {
        background: #ef4444 !important;
        animation: pulse 1s infinite;
    }
    
    .voice-btn.speaking {
        background: #10b981 !important;
        animation: pulse 1s infinite;
    }
    
    .voice-indicator {
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 12px 20px;
        border-radius: 12px;
        color: white;
        font-weight: 600;
        z-index: 1000;
        display: flex;
        align-items: center;
        gap: 8px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.15);
    }
    
    .voice-indicator.recording {
        background: #ef4444;
        animation: pulse 2s infinite;
    }
    
    .voice-indicator.processing {
        background: #3b82f6;
        animation: pulse 1.5s infinite;
    }
    
    @keyframes pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.8; transform: scale(1.05); }
    }
`;
document.head.appendChild(voiceStyles);
