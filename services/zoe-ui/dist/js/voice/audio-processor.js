/**
 * Audio Processor (AudioWorklet)
 * ===============================
 * 
 * Real-time audio processing for voice activity detection.
 * Runs in a separate thread for better performance.
 */

class VadProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        
        // Configuration
        this.threshold = 0.02;      // Energy threshold for voice
        this.minSpeechMs = 100;     // Min speech duration
        this.maxSilenceMs = 1500;   // Max silence before end
        
        // State
        this.isActive = false;
        this.speechStartTime = 0;
        this.silenceStartTime = 0;
        this.frameCount = 0;
        
        // Energy smoothing
        this.energyHistory = [];
        this.energyWindowSize = 10;
        
        // Listen for config updates from main thread
        this.port.onmessage = (event) => {
            if (event.data.type === 'config') {
                this.threshold = event.data.threshold ?? this.threshold;
                this.minSpeechMs = event.data.minSpeechMs ?? this.minSpeechMs;
                this.maxSilenceMs = event.data.maxSilenceMs ?? this.maxSilenceMs;
            }
        };
    }
    
    /**
     * Process audio frame
     */
    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (!input || !input[0]) return true;
        
        const samples = input[0];
        
        // Calculate RMS energy
        let sum = 0;
        for (let i = 0; i < samples.length; i++) {
            sum += samples[i] * samples[i];
        }
        const energy = Math.sqrt(sum / samples.length);
        
        // Add to history for smoothing
        this.energyHistory.push(energy);
        if (this.energyHistory.length > this.energyWindowSize) {
            this.energyHistory.shift();
        }
        
        // Calculate smoothed energy
        const smoothedEnergy = this.energyHistory.reduce((a, b) => a + b, 0) 
            / this.energyHistory.length;
        
        // Update state
        this.updateVadState(smoothedEnergy);
        
        this.frameCount++;
        
        return true;
    }
    
    /**
     * Update voice activity state
     */
    updateVadState(energy) {
        const now = currentTime * 1000;  // Convert to ms
        const aboveThreshold = energy > this.threshold;
        
        if (aboveThreshold && !this.isActive) {
            // Potential speech start
            if (this.speechStartTime === 0) {
                this.speechStartTime = now;
            } else if (now - this.speechStartTime > this.minSpeechMs) {
                // Confirmed speech
                this.isActive = true;
                this.silenceStartTime = 0;
                this.sendVadEvent(true, energy);
            }
        } else if (!aboveThreshold && this.isActive) {
            // Potential speech end
            if (this.silenceStartTime === 0) {
                this.silenceStartTime = now;
            } else if (now - this.silenceStartTime > this.maxSilenceMs) {
                // Confirmed silence
                this.isActive = false;
                this.speechStartTime = 0;
                this.sendVadEvent(false, energy);
            }
        } else if (aboveThreshold && this.isActive) {
            // Continued speech
            this.silenceStartTime = 0;
        } else if (!aboveThreshold && !this.isActive) {
            // Continued silence
            this.speechStartTime = 0;
        }
    }
    
    /**
     * Send VAD event to main thread
     */
    sendVadEvent(active, confidence) {
        this.port.postMessage({
            type: 'vad',
            active: active,
            confidence: Math.min(1.0, confidence * 10),  // Scale to 0-1
            timestamp: currentTime * 1000
        });
    }
}

// Register the processor
registerProcessor('vad-processor', VadProcessor);

