"""
Wake Word Detection Service
===========================

Detects wake words like "Hey Zoe" using Porcupine or OpenWakeWord.
Supports both edge devices (native) and browser (via WebAssembly).
"""

import os
import asyncio
import logging
from typing import Optional, Callable, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# Check for Porcupine (Picovoice)
try:
    import pvporcupine
    PORCUPINE_AVAILABLE = True
except ImportError:
    PORCUPINE_AVAILABLE = False
    logger.debug("pvporcupine not installed - using fallback")

# Check for OpenWakeWord
try:
    from openwakeword.model import Model as OWWModel
    OPENWAKEWORD_AVAILABLE = True
except ImportError:
    OPENWAKEWORD_AVAILABLE = False
    logger.debug("openwakeword not installed - using fallback")


class WakeWordEngine(str, Enum):
    """Available wake word detection engines."""
    PORCUPINE = "porcupine"
    OPENWAKEWORD = "openwakeword"
    BROWSER = "browser"  # Browser-based via WASM
    NONE = "none"


@dataclass
class WakeWordEvent:
    """Event when wake word is detected."""
    keyword: str
    confidence: float
    timestamp: float
    engine: WakeWordEngine


class WakeWordDetector:
    """
    Wake word detection service.
    
    Supports multiple backends:
    - Porcupine: Commercial solution, very accurate
    - OpenWakeWord: Open source, good accuracy
    - Browser: Client-side detection via WASM
    
    Usage:
        detector = WakeWordDetector()
        await detector.start()
        detector.on_wake_word(lambda event: print(f"Heard: {event.keyword}"))
    """
    
    def __init__(
        self,
        keywords: Optional[List[str]] = None,
        engine: Optional[WakeWordEngine] = None
    ):
        """
        Initialize wake word detector.
        
        Args:
            keywords: List of wake words to detect (default: ["hey zoe", "zoe"])
            engine: Preferred engine (auto-detected if not specified)
        """
        self.keywords = keywords or ["hey zoe", "zoe"]
        self.engine = engine or self._detect_engine()
        
        self._running = False
        self._callbacks: List[Callable[[WakeWordEvent], None]] = []
        self._detector = None
        self._audio_stream = None
    
    def _detect_engine(self) -> WakeWordEngine:
        """Auto-detect the best available engine."""
        if PORCUPINE_AVAILABLE and os.getenv("PORCUPINE_ACCESS_KEY"):
            return WakeWordEngine.PORCUPINE
        elif OPENWAKEWORD_AVAILABLE:
            return WakeWordEngine.OPENWAKEWORD
        else:
            return WakeWordEngine.NONE
    
    async def start(self) -> bool:
        """
        Start wake word detection.
        
        Returns:
            True if started successfully
        """
        if self._running:
            return True
        
        try:
            if self.engine == WakeWordEngine.PORCUPINE:
                return await self._start_porcupine()
            elif self.engine == WakeWordEngine.OPENWAKEWORD:
                return await self._start_openwakeword()
            elif self.engine == WakeWordEngine.BROWSER:
                # Browser detection is handled client-side
                logger.info("Wake word detection handled by browser")
                return True
            else:
                logger.warning("No wake word engine available")
                return False
        except Exception as e:
            logger.error(f"Failed to start wake word detection: {e}")
            return False
    
    async def stop(self) -> None:
        """Stop wake word detection."""
        self._running = False
        
        if self._detector:
            try:
                if hasattr(self._detector, 'delete'):
                    self._detector.delete()
                self._detector = None
            except Exception as e:
                logger.warning(f"Error stopping detector: {e}")
        
        if self._audio_stream:
            try:
                self._audio_stream.close()
                self._audio_stream = None
            except Exception:
                pass
    
    def on_wake_word(self, callback: Callable[[WakeWordEvent], None]) -> None:
        """Register callback for wake word detection."""
        self._callbacks.append(callback)
    
    def _emit_wake_word(self, keyword: str, confidence: float = 1.0) -> None:
        """Emit wake word event to all callbacks."""
        import time
        event = WakeWordEvent(
            keyword=keyword,
            confidence=confidence,
            timestamp=time.time(),
            engine=self.engine
        )
        
        logger.info(f"Wake word detected: {keyword} (confidence: {confidence:.2f})")
        
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Wake word callback error: {e}")
    
    # ========================================
    # Porcupine Implementation
    # ========================================
    
    async def _start_porcupine(self) -> bool:
        """Start Porcupine wake word detection."""
        access_key = os.getenv("PORCUPINE_ACCESS_KEY")
        if not access_key:
            logger.error("PORCUPINE_ACCESS_KEY not set")
            return False
        
        try:
            # Create Porcupine instance
            # Note: Custom keywords require training via Picovoice console
            self._detector = pvporcupine.create(
                access_key=access_key,
                keywords=["porcupine"],  # Use built-in keyword
                # For custom "hey zoe" would need:
                # keyword_paths=["/path/to/hey-zoe.ppn"]
            )
            
            # Start audio capture
            self._running = True
            asyncio.create_task(self._porcupine_loop())
            
            logger.info("Porcupine wake word detection started")
            return True
            
        except Exception as e:
            logger.error(f"Porcupine initialization failed: {e}")
            return False
    
    async def _porcupine_loop(self):
        """Porcupine audio processing loop."""
        try:
            import pyaudio
            
            pa = pyaudio.PyAudio()
            self._audio_stream = pa.open(
                rate=self._detector.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=self._detector.frame_length
            )
            
            while self._running:
                # Read audio frame
                pcm = self._audio_stream.read(
                    self._detector.frame_length,
                    exception_on_overflow=False
                )
                pcm = struct.unpack_from("h" * self._detector.frame_length, pcm)
                
                # Process with Porcupine
                keyword_index = self._detector.process(pcm)
                
                if keyword_index >= 0:
                    self._emit_wake_word("hey zoe", 1.0)
                
                # Small delay to prevent CPU spinning
                await asyncio.sleep(0.01)
                
        except Exception as e:
            logger.error(f"Porcupine loop error: {e}")
        finally:
            self._running = False
    
    # ========================================
    # OpenWakeWord Implementation
    # ========================================
    
    async def _start_openwakeword(self) -> bool:
        """Start OpenWakeWord wake word detection."""
        try:
            # Load model
            self._detector = OWWModel(
                wakeword_models=["hey_jarvis"],  # Use similar wake word as template
                inference_framework="onnx"
            )
            
            # Start audio capture
            self._running = True
            asyncio.create_task(self._openwakeword_loop())
            
            logger.info("OpenWakeWord detection started")
            return True
            
        except Exception as e:
            logger.error(f"OpenWakeWord initialization failed: {e}")
            return False
    
    async def _openwakeword_loop(self):
        """OpenWakeWord audio processing loop."""
        try:
            import pyaudio
            import numpy as np
            
            pa = pyaudio.PyAudio()
            self._audio_stream = pa.open(
                rate=16000,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=1280
            )
            
            while self._running:
                # Read audio
                audio = self._audio_stream.read(1280, exception_on_overflow=False)
                audio = np.frombuffer(audio, dtype=np.int16)
                
                # Predict
                prediction = self._detector.predict(audio)
                
                # Check for activation
                for model_name, score in prediction.items():
                    if score > 0.5:  # Threshold
                        self._emit_wake_word("hey zoe", float(score))
                
                await asyncio.sleep(0.01)
                
        except Exception as e:
            logger.error(f"OpenWakeWord loop error: {e}")
        finally:
            self._running = False
    
    # ========================================
    # Browser WASM Support
    # ========================================
    
    def get_browser_config(self) -> dict:
        """
        Get configuration for browser-side wake word detection.
        
        Returns config for initializing WASM-based detection in browser.
        """
        return {
            "keywords": self.keywords,
            "sensitivity": 0.5,
            "engine": "porcupine_wasm" if PORCUPINE_AVAILABLE else "openwakeword_wasm",
            "wasm_path": "/js/wakeword/",
            "model_path": "/js/wakeword/models/"
        }
    
    async def handle_browser_detection(self, data: dict) -> None:
        """
        Handle wake word detection from browser.
        
        Called when browser-side WASM detects wake word.
        
        Args:
            data: Detection data from browser {keyword, confidence, timestamp}
        """
        self._emit_wake_word(
            keyword=data.get("keyword", "hey zoe"),
            confidence=data.get("confidence", 1.0)
        )


# Singleton instance
_wake_word_detector: Optional[WakeWordDetector] = None


def get_wake_word_detector() -> WakeWordDetector:
    """Get the singleton wake word detector instance."""
    global _wake_word_detector
    if _wake_word_detector is None:
        _wake_word_detector = WakeWordDetector()
    return _wake_word_detector

