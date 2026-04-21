"""
Barge-In Support
================

Allows users to interrupt Zoe while she's speaking or playing music.
Handles voice activity detection and interruption logic.
"""

import asyncio
import logging
from typing import Optional, Callable, List
from dataclasses import dataclass
from enum import Enum
import time

logger = logging.getLogger(__name__)


class InterruptType(str, Enum):
    """Types of interruptions."""
    WAKE_WORD = "wake_word"      # User said wake word
    VOICE = "voice"              # User started speaking
    BUTTON = "button"            # User pressed button/key
    MANUAL = "manual"            # Manual API call


class ZoeState(str, Enum):
    """Zoe's current activity state."""
    IDLE = "idle"                # Not doing anything
    LISTENING = "listening"      # Actively listening to user
    PROCESSING = "processing"    # Processing user request
    SPEAKING = "speaking"        # Speaking response
    PLAYING_MUSIC = "playing"    # Playing music


@dataclass
class InterruptEvent:
    """Event when user interrupts Zoe."""
    interrupt_type: InterruptType
    previous_state: ZoeState
    timestamp: float
    confidence: float = 1.0


@dataclass
class BargeInConfig:
    """Configuration for barge-in behavior."""
    allow_during_speech: bool = True    # Can interrupt while Zoe speaks
    allow_during_music: bool = True     # Can interrupt during music
    vad_threshold: float = 0.5          # Voice activity threshold
    min_silence_ms: int = 500           # Min silence before processing
    interrupt_sound: bool = True         # Play sound on interrupt


class BargeInController:
    """
    Barge-in controller for voice interruptions.
    
    Manages the ability for users to interrupt Zoe:
    - Wake word detection triggers interrupt
    - Voice activity during Zoe's speech triggers interrupt
    - Button press triggers interrupt
    
    Usage:
        controller = BargeInController()
        controller.on_interrupt(lambda event: handle_interrupt(event))
        await controller.start()
    """
    
    def __init__(self, config: Optional[BargeInConfig] = None):
        """Initialize barge-in controller."""
        self.config = config or BargeInConfig()
        self.state = ZoeState.IDLE
        self._running = False
        
        self._callbacks: List[Callable[[InterruptEvent], None]] = []
        self._vad_active = False
        self._last_voice_time = 0.0
        
        # Connected services
        self._wake_word_detector = None
        self._audio_ducker = None
        self._tts_controller = None
        self._music_controller = None
    
    def set_services(
        self,
        wake_word_detector=None,
        audio_ducker=None,
        tts_controller=None,
        music_controller=None
    ) -> None:
        """Connect related services."""
        self._wake_word_detector = wake_word_detector
        self._audio_ducker = audio_ducker
        self._tts_controller = tts_controller
        self._music_controller = music_controller
    
    async def start(self) -> None:
        """Start barge-in monitoring."""
        if self._running:
            return
        
        self._running = True
        
        # Connect to wake word detector if available
        if self._wake_word_detector:
            self._wake_word_detector.on_wake_word(self._handle_wake_word)
        
        logger.info("Barge-in controller started")
    
    async def stop(self) -> None:
        """Stop barge-in monitoring."""
        self._running = False
        logger.info("Barge-in controller stopped")
    
    def on_interrupt(self, callback: Callable[[InterruptEvent], None]) -> None:
        """Register callback for interrupt events."""
        self._callbacks.append(callback)
    
    def set_state(self, state: ZoeState) -> None:
        """Update Zoe's current state."""
        old_state = self.state
        self.state = state
        logger.debug(f"Zoe state: {old_state.value} -> {state.value}")
    
    def _emit_interrupt(
        self,
        interrupt_type: InterruptType,
        confidence: float = 1.0
    ) -> None:
        """Emit interrupt event."""
        event = InterruptEvent(
            interrupt_type=interrupt_type,
            previous_state=self.state,
            timestamp=time.time(),
            confidence=confidence
        )
        
        logger.info(f"Interrupt: {interrupt_type.value} during {self.state.value}")
        
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Interrupt callback error: {e}")
    
    def _handle_wake_word(self, wake_event) -> None:
        """Handle wake word detection."""
        if not self._should_allow_interrupt(InterruptType.WAKE_WORD):
            logger.debug("Wake word ignored - interrupt not allowed")
            return
        
        # Stop current activity
        asyncio.create_task(self._interrupt_current_activity())
        
        # Emit interrupt event
        self._emit_interrupt(
            InterruptType.WAKE_WORD,
            confidence=wake_event.confidence
        )
    
    def _should_allow_interrupt(self, interrupt_type: InterruptType) -> bool:
        """Check if interrupt is allowed in current state."""
        if self.state == ZoeState.IDLE:
            return True  # Always allow when idle
        
        if self.state == ZoeState.LISTENING:
            return True  # Allow during listening (might be re-activation)
        
        if self.state == ZoeState.PROCESSING:
            return True  # Allow during processing (cancel current)
        
        if self.state == ZoeState.SPEAKING:
            return self.config.allow_during_speech
        
        if self.state == ZoeState.PLAYING_MUSIC:
            return self.config.allow_during_music
        
        return True
    
    async def _interrupt_current_activity(self) -> None:
        """Stop current activity based on state."""
        # Duck audio if playing music
        if self._audio_ducker and self.state == ZoeState.PLAYING_MUSIC:
            await self._audio_ducker.duck("barge_in")
        
        # Stop TTS if speaking
        if self._tts_controller and self.state == ZoeState.SPEAKING:
            try:
                await self._tts_controller.stop()
            except Exception as e:
                logger.error(f"Failed to stop TTS: {e}")
        
        # Transition to listening
        self.set_state(ZoeState.LISTENING)
    
    async def handle_voice_activity(self, is_active: bool, confidence: float = 1.0) -> None:
        """
        Handle voice activity detection events.
        
        Called from browser when VAD detects user speaking.
        
        Args:
            is_active: Whether voice is currently detected
            confidence: Confidence of detection
        """
        if not self._running:
            return
        
        if is_active:
            self._vad_active = True
            self._last_voice_time = time.time()
            
            # Check if this should trigger interrupt
            if confidence >= self.config.vad_threshold:
                if self._should_allow_interrupt(InterruptType.VOICE):
                    if self.state in [ZoeState.SPEAKING, ZoeState.PLAYING_MUSIC]:
                        await self._interrupt_current_activity()
                        self._emit_interrupt(InterruptType.VOICE, confidence)
        else:
            self._vad_active = False
            
            # If we were listening and voice stopped, check for processing
            if self.state == ZoeState.LISTENING:
                silence_duration = (time.time() - self._last_voice_time) * 1000
                if silence_duration >= self.config.min_silence_ms:
                    # Ready to process - this will be handled by STT
                    pass
    
    async def trigger_interrupt(
        self,
        interrupt_type: InterruptType = InterruptType.MANUAL
    ) -> None:
        """
        Manually trigger an interrupt.
        
        Can be called from button press, keyboard shortcut, etc.
        """
        if not self._should_allow_interrupt(interrupt_type):
            return
        
        await self._interrupt_current_activity()
        self._emit_interrupt(interrupt_type)
    
    def get_state(self) -> dict:
        """Get current barge-in state."""
        return {
            "state": self.state.value,
            "vad_active": self._vad_active,
            "last_voice_time": self._last_voice_time,
            "config": {
                "allow_during_speech": self.config.allow_during_speech,
                "allow_during_music": self.config.allow_during_music
            }
        }


# Singleton instance
_barge_in_controller: Optional[BargeInController] = None


def get_barge_in_controller() -> BargeInController:
    """Get the singleton barge-in controller instance."""
    global _barge_in_controller
    if _barge_in_controller is None:
        _barge_in_controller = BargeInController()
    return _barge_in_controller

