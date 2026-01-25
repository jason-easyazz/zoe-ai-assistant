/**
 * Voice Controller
 * =================
 * 
 * Browser-side voice control for Zoe.
 * Handles wake word detection, VAD, and audio ducking.
 */

class VoiceController {
    constructor() {
        this.ws = null;
        this.isConnected = false;
        this.config = null;
        
        // State
        this.isListening = false;
        this.isVadActive = false;
        this.duckingLevel = 1.0;
        
        // Audio elements to duck
        this.audioElements = [];
        
        // Callbacks
        this.callbacks = {
            wakeWord: [],
            stateChange: [],
            interrupt: [],
            ducking: []
        };
        
        // Wake word detection
        this.wakeWordDetector = null;
        
        // VAD (Voice Activity Detection)
        this.vadProcessor = null;
        this.audioContext = null;
        this.mediaStream = null;
    }
    
    /**
     * Initialize voice controller
     */
    async init() {
        try {
            // Load config from server
            await this.loadConfig();
            
            // Connect WebSocket
            await this.connectWebSocket();
            
            // Find audio elements
            this.discoverAudioElements();
            
            console.log('[Voice] Controller initialized');
            return true;
        } catch (error) {
            console.error('[Voice] Initialization failed:', error);
            return false;
        }
    }
    
    /**
     * Load voice configuration from server
     */
    async loadConfig() {
        try {
            const response = await fetch('/api/voice/config');
            if (response.ok) {
                this.config = await response.json();
                console.log('[Voice] Config loaded:', this.config);
            }
        } catch (error) {
            console.warn('[Voice] Could not load config, using defaults');
            this.config = {
                wake_word: { keywords: ['hey zoe'], sensitivity: 0.5 },
                vad: { threshold: 0.5, min_speech_ms: 100, max_silence_ms: 1500 },
                ducking: { level: 0.2, fade_ms: 200 }
            };
        }
    }
    
    /**
     * Connect to voice WebSocket
     */
    async connectWebSocket() {
        return new Promise((resolve, reject) => {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const sessionId = window.zoeAuth?.getSession?.() || 'anonymous';
            
            this.ws = new WebSocket(
                `${protocol}//${window.location.host}/api/voice/ws?session_id=${sessionId}`
            );
            
            this.ws.onopen = () => {
                this.isConnected = true;
                console.log('[Voice] WebSocket connected');
                resolve();
            };
            
            this.ws.onclose = () => {
                this.isConnected = false;
                console.log('[Voice] WebSocket disconnected');
                // Attempt reconnect after delay
                setTimeout(() => this.connectWebSocket(), 5000);
            };
            
            this.ws.onerror = (error) => {
                console.error('[Voice] WebSocket error:', error);
                reject(error);
            };
            
            this.ws.onmessage = (event) => {
                this.handleMessage(JSON.parse(event.data));
            };
        });
    }
    
    /**
     * Handle incoming WebSocket message
     */
    handleMessage(data) {
        switch (data.type) {
            case 'ducking':
                this.handleDuckingUpdate(data);
                break;
            
            case 'state':
                this.handleStateUpdate(data);
                break;
            
            case 'interrupt':
                this.handleInterruptEvent(data);
                break;
            
            case 'pong':
                // Keepalive response
                break;
            
            default:
                console.log('[Voice] Unknown message:', data);
        }
    }
    
    /**
     * Handle ducking state update
     */
    handleDuckingUpdate(data) {
        this.duckingLevel = data.level;
        
        // Apply to all audio elements
        for (const audio of this.audioElements) {
            this.applyDucking(audio, data.level);
        }
        
        // Notify callbacks
        for (const callback of this.callbacks.ducking) {
            callback(data.state, data.level);
        }
        
        console.log(`[Voice] Ducking: ${data.state} (level: ${data.level})`);
    }
    
    /**
     * Handle state update from server
     */
    handleStateUpdate(data) {
        for (const callback of this.callbacks.stateChange) {
            callback(data);
        }
    }
    
    /**
     * Handle interrupt event
     */
    handleInterruptEvent(data) {
        for (const callback of this.callbacks.interrupt) {
            callback(data);
        }
    }
    
    /**
     * Apply volume ducking to audio element
     */
    applyDucking(audio, level) {
        if (!audio) return;
        
        // Store original volume if not already stored
        if (audio._originalVolume === undefined) {
            audio._originalVolume = audio.volume;
        }
        
        // Apply ducking
        audio.volume = audio._originalVolume * level;
    }
    
    /**
     * Discover all audio elements on page
     */
    discoverAudioElements() {
        // Clear existing
        this.audioElements = [];
        
        // Find all audio/video elements
        const elements = document.querySelectorAll('audio, video');
        elements.forEach(el => this.audioElements.push(el));
        
        // Also include window.ZOE_SHARED_AUDIO if exists
        if (window.ZOE_SHARED_AUDIO) {
            this.audioElements.push(window.ZOE_SHARED_AUDIO);
        }
        
        console.log(`[Voice] Found ${this.audioElements.length} audio elements`);
    }
    
    /**
     * Register an audio element for ducking
     */
    registerAudio(audio) {
        if (!this.audioElements.includes(audio)) {
            this.audioElements.push(audio);
        }
    }
    
    // ========================================
    // Wake Word Detection
    // ========================================
    
    /**
     * Start wake word detection
     */
    async startWakeWordDetection() {
        if (this.isListening) return;
        
        try {
            // Request microphone access
            this.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true
                }
            });
            
            // Create audio context
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: 16000
            });
            
            // Set up audio processing
            await this.setupAudioProcessing();
            
            this.isListening = true;
            console.log('[Voice] Wake word detection started');
            
        } catch (error) {
            console.error('[Voice] Failed to start wake word detection:', error);
            throw error;
        }
    }
    
    /**
     * Stop wake word detection
     */
    stopWakeWordDetection() {
        this.isListening = false;
        
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }
        
        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }
        
        console.log('[Voice] Wake word detection stopped');
    }
    
    /**
     * Set up audio processing for wake word and VAD
     */
    async setupAudioProcessing() {
        const source = this.audioContext.createMediaStreamSource(this.mediaStream);
        
        // Use AudioWorklet for better performance
        try {
            await this.audioContext.audioWorklet.addModule('/js/voice/audio-processor.js');
            
            this.vadProcessor = new AudioWorkletNode(this.audioContext, 'vad-processor');
            
            this.vadProcessor.port.onmessage = (event) => {
                this.handleVadMessage(event.data);
            };
            
            source.connect(this.vadProcessor);
            
        } catch (error) {
            console.warn('[Voice] AudioWorklet not available, using ScriptProcessor');
            this.setupScriptProcessor(source);
        }
    }
    
    /**
     * Fallback to ScriptProcessor for browsers without AudioWorklet
     */
    setupScriptProcessor(source) {
        const processor = this.audioContext.createScriptProcessor(4096, 1, 1);
        
        let vadBuffer = [];
        let vadTimeout = null;
        
        processor.onaudioprocess = (event) => {
            const inputData = event.inputBuffer.getChannelData(0);
            
            // Calculate RMS energy
            let sum = 0;
            for (let i = 0; i < inputData.length; i++) {
                sum += inputData[i] * inputData[i];
            }
            const rms = Math.sqrt(sum / inputData.length);
            
            // Voice activity detection (simple energy-based)
            const isActive = rms > (this.config?.vad?.threshold || 0.02);
            
            if (isActive !== this.isVadActive) {
                this.isVadActive = isActive;
                this.sendVadEvent(isActive, rms);
            }
            
            // Keyword detection would go here
            // For production, use Porcupine WASM or similar
        };
        
        source.connect(processor);
        processor.connect(this.audioContext.destination);
    }
    
    /**
     * Handle VAD processor message
     */
    handleVadMessage(data) {
        if (data.type === 'vad') {
            if (data.active !== this.isVadActive) {
                this.isVadActive = data.active;
                this.sendVadEvent(data.active, data.confidence);
            }
        } else if (data.type === 'wake_word') {
            this.handleWakeWord(data.keyword, data.confidence);
        }
    }
    
    /**
     * Send VAD event to server
     */
    sendVadEvent(active, confidence) {
        if (this.isConnected) {
            this.ws.send(JSON.stringify({
                type: 'vad',
                active: active,
                confidence: confidence
            }));
        }
    }
    
    /**
     * Handle wake word detection
     */
    handleWakeWord(keyword, confidence) {
        console.log(`[Voice] Wake word detected: ${keyword} (${confidence})`);
        
        // Send to server
        if (this.isConnected) {
            this.ws.send(JSON.stringify({
                type: 'wake_word',
                keyword: keyword,
                confidence: confidence
            }));
        }
        
        // Notify callbacks
        for (const callback of this.callbacks.wakeWord) {
            callback(keyword, confidence);
        }
    }
    
    // ========================================
    // Manual Controls
    // ========================================
    
    /**
     * Manually trigger interrupt (e.g., from button)
     */
    triggerInterrupt(source = 'button') {
        if (this.isConnected) {
            this.ws.send(JSON.stringify({
                type: 'interrupt',
                source: source
            }));
        }
    }
    
    /**
     * Request current state from server
     */
    requestState() {
        if (this.isConnected) {
            this.ws.send(JSON.stringify({
                type: 'state_request'
            }));
        }
    }
    
    // ========================================
    // Event Handlers
    // ========================================
    
    /**
     * Register callback for wake word detection
     */
    onWakeWord(callback) {
        this.callbacks.wakeWord.push(callback);
    }
    
    /**
     * Register callback for state changes
     */
    onStateChange(callback) {
        this.callbacks.stateChange.push(callback);
    }
    
    /**
     * Register callback for interrupt events
     */
    onInterrupt(callback) {
        this.callbacks.interrupt.push(callback);
    }
    
    /**
     * Register callback for ducking changes
     */
    onDucking(callback) {
        this.callbacks.ducking.push(callback);
    }
}

// Create and export singleton instance
window.VoiceController = new VoiceController();

// Auto-initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Initialize after a short delay to let other scripts load
    setTimeout(() => {
        window.VoiceController.init().then(success => {
            if (success) {
                console.log('[Voice] Controller ready');
            }
        });
    }, 500);
});

