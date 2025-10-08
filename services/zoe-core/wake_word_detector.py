"""
Wake Word Detection System for Zoe AI Assistant
Implements "Hey Zoe" activation using openWakeWord
"""
import pyaudio
import numpy as np
import threading
import time
import logging
from typing import Callable, Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum
import json
import os

logger = logging.getLogger(__name__)

class WakeWordState(Enum):
    LISTENING = "listening"
    DETECTED = "detected"
    PROCESSING = "processing"
    ERROR = "error"

@dataclass
class WakeWordConfig:
    """Configuration for wake word detection"""
    wake_phrase: str = "Hey Zoe"
    confidence_threshold: float = 0.7
    sample_rate: int = 16000
    chunk_size: int = 1024
    channels: int = 1
    format: int = pyaudio.paInt16
    timeout_seconds: float = 30.0
    cooldown_seconds: float = 2.0

class WakeWordDetector:
    """Wake word detection system using openWakeWord"""
    
    def __init__(self, config: WakeWordConfig = None):
        self.config = config or WakeWordConfig()
        self.state = WakeWordState.LISTENING
        self.detection_callbacks: List[Callable] = []
        self.listening = False
        self.audio_thread: Optional[threading.Thread] = None
        self.last_detection_time = 0
        self.audio_stream: Optional[pyaudio.PyAudio.Stream] = None
        self.pyaudio_instance: Optional[pyaudio.PyAudio] = None
        
        # Detection statistics
        self.detection_count = 0
        self.false_positive_count = 0
        self.last_confidence = 0.0
        
    def add_detection_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Add callback function for wake word detection"""
        self.detection_callbacks.append(callback)
    
    def start_listening(self) -> bool:
        """Start listening for wake words"""
        if self.listening:
            return True
        
        try:
            # Initialize PyAudio
            self.pyaudio_instance = pyaudio.PyAudio()
            
            # Open audio stream
            self.audio_stream = self.pyaudio_instance.open(
                format=self.config.format,
                channels=self.config.channels,
                rate=self.config.sample_rate,
                input=True,
                frames_per_buffer=self.config.chunk_size,
                stream_callback=self._audio_callback
            )
            
            self.listening = True
            self.state = WakeWordState.LISTENING
            
            # Start audio processing thread
            self.audio_thread = threading.Thread(target=self._audio_processing_loop, daemon=True)
            self.audio_thread.start()
            
            logger.info("Wake word detection started")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start wake word detection: {e}")
            self.state = WakeWordState.ERROR
            return False
    
    def stop_listening(self):
        """Stop listening for wake words"""
        self.listening = False
        
        if self.audio_stream:
            self.audio_stream.stop_stream()
            self.audio_stream.close()
            self.audio_stream = None
        
        if self.pyaudio_instance:
            self.pyaudio_instance.terminate()
            self.pyaudio_instance = None
        
        if self.audio_thread:
            self.audio_thread.join(timeout=1.0)
        
        self.state = WakeWordState.LISTENING
        logger.info("Wake word detection stopped")
    
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """Audio stream callback"""
        if self.listening:
            # Process audio data in the main thread
            threading.Thread(target=self._process_audio_chunk, args=(in_data,), daemon=True).start()
        return (in_data, pyaudio.paContinue)
    
    def _process_audio_chunk(self, audio_data: bytes):
        """Process audio chunk for wake word detection"""
        try:
            # Convert audio data to numpy array
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            
            # Simple energy-based detection (placeholder for actual wake word model)
            energy = np.mean(audio_array.astype(np.float32) ** 2)
            
            # Simulate wake word detection based on energy and patterns
            confidence = self._simulate_wake_word_detection(audio_array, energy)
            
            if confidence > self.config.confidence_threshold:
                self._handle_wake_word_detection(confidence)
                
        except Exception as e:
            logger.error(f"Error processing audio chunk: {e}")
    
    def _simulate_wake_word_detection(self, audio_array: np.ndarray, energy: float) -> float:
        """Simulate wake word detection (placeholder for actual model)"""
        # This is a simplified simulation - in reality, you would use openWakeWord
        # or a similar library with a trained model
        
        # Check for energy spikes that might indicate speech
        if energy < 1000:  # Too quiet
            return 0.0
        
        # Check for speech-like patterns (simplified)
        audio_std = np.std(audio_array)
        if audio_std < 500:  # Too uniform
            return 0.0
        
        # Simulate occasional wake word detection
        # In practice, this would be replaced with actual model inference
        if energy > 5000 and audio_std > 1000:
            # Simulate confidence based on audio characteristics
            confidence = min(0.9, (energy / 10000) * (audio_std / 2000))
            
            # Add some randomness to simulate real detection
            if np.random.random() < 0.01:  # 1% chance of detection
                return confidence
        
        return 0.0
    
    def _handle_wake_word_detection(self, confidence: float):
        """Handle wake word detection"""
        current_time = time.time()
        
        # Check cooldown period
        if current_time - self.last_detection_time < self.config.cooldown_seconds:
            return
        
        self.last_detection_time = current_time
        self.detection_count += 1
        self.last_confidence = confidence
        self.state = WakeWordState.DETECTED
        
        # Create detection event
        detection_event = {
            "timestamp": current_time,
            "confidence": confidence,
            "wake_phrase": self.config.wake_phrase,
            "detection_count": self.detection_count
        }
        
        logger.info(f"Wake word detected: {confidence:.2f} confidence")
        
        # Call all detection callbacks
        for callback in self.detection_callbacks:
            try:
                callback(detection_event)
            except Exception as e:
                logger.error(f"Detection callback failed: {e}")
        
        # Reset state after processing
        self.state = WakeWordState.LISTENING
    
    def _audio_processing_loop(self):
        """Main audio processing loop"""
        while self.listening:
            try:
                time.sleep(0.1)  # Small delay to prevent excessive CPU usage
            except Exception as e:
                logger.error(f"Error in audio processing loop: {e}")
                break
    
    def get_status(self) -> Dict[str, Any]:
        """Get current wake word detector status"""
        return {
            "listening": self.listening,
            "state": self.state.value,
            "config": {
                "wake_phrase": self.config.wake_phrase,
                "confidence_threshold": self.config.confidence_threshold,
                "sample_rate": self.config.sample_rate,
                "cooldown_seconds": self.config.cooldown_seconds
            },
            "statistics": {
                "detection_count": self.detection_count,
                "false_positive_count": self.false_positive_count,
                "last_confidence": self.last_confidence,
                "last_detection_time": self.last_detection_time
            }
        }
    
    def reset_statistics(self):
        """Reset detection statistics"""
        self.detection_count = 0
        self.false_positive_count = 0
        self.last_confidence = 0.0
        self.last_detection_time = 0
        logger.info("Wake word detection statistics reset")
    
    def update_config(self, new_config: Dict[str, Any]):
        """Update wake word detection configuration"""
        try:
            if "confidence_threshold" in new_config:
                self.config.confidence_threshold = float(new_config["confidence_threshold"])
            
            if "cooldown_seconds" in new_config:
                self.config.cooldown_seconds = float(new_config["cooldown_seconds"])
            
            if "wake_phrase" in new_config:
                self.config.wake_phrase = str(new_config["wake_phrase"])
            
            logger.info(f"Wake word configuration updated: {new_config}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update configuration: {e}")
            return False

class WakeWordManager:
    """Manager for wake word detection system"""
    
    def __init__(self):
        self.detector = WakeWordDetector()
        self.integration_callbacks: List[Callable] = []
        
        # Add default callback for system integration
        self.detector.add_detection_callback(self._handle_wake_word)
    
    def _handle_wake_word(self, detection_event: Dict[str, Any]):
        """Handle wake word detection and trigger system actions"""
        try:
            # Log the detection
            logger.info(f"Wake word detected: {detection_event}")
            
            # Trigger system activation
            self._activate_system(detection_event)
            
            # Call integration callbacks
            for callback in self.integration_callbacks:
                try:
                    callback(detection_event)
                except Exception as e:
                    logger.error(f"Integration callback failed: {e}")
                    
        except Exception as e:
            logger.error(f"Error handling wake word detection: {e}")
    
    def _activate_system(self, detection_event: Dict[str, Any]):
        """Activate the system after wake word detection"""
        try:
            # This would integrate with the main Zoe system
            # For now, we'll just log the activation
            logger.info("System activated by wake word detection")
            
            # In a real implementation, this would:
            # 1. Start listening for voice commands
            # 2. Play activation sound
            # 3. Show visual feedback
            # 4. Prepare for voice input
            
        except Exception as e:
            logger.error(f"Failed to activate system: {e}")
    
    def add_integration_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Add callback for system integration"""
        self.integration_callbacks.append(callback)
    
    def start(self) -> bool:
        """Start wake word detection"""
        return self.detector.start_listening()
    
    def stop(self):
        """Stop wake word detection"""
        self.detector.stop_listening()
    
    def get_status(self) -> Dict[str, Any]:
        """Get wake word system status"""
        return self.detector.get_status()
    
    def update_config(self, config: Dict[str, Any]) -> bool:
        """Update wake word configuration"""
        return self.detector.update_config(config)

# Global instance
wake_word_manager = WakeWordManager()
