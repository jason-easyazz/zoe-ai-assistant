"""
Audio Ducking Service
=====================

Manages audio volume reduction when voice interaction is active.
Ensures music volume lowers when wake word is detected or voice input begins.
"""

import asyncio
import logging
from typing import Optional, Callable, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class DuckingState(str, Enum):
    """Audio ducking states."""
    NORMAL = "normal"       # Full volume
    DUCKED = "ducked"       # Reduced volume for voice
    MUTED = "muted"         # Completely muted


@dataclass
class DuckingConfig:
    """Configuration for audio ducking."""
    duck_level: float = 0.2      # Volume level when ducked (0-1)
    fade_in_ms: int = 500        # Fade in duration
    fade_out_ms: int = 200       # Fade out duration (faster to duck)
    hold_time_ms: int = 2000     # How long to hold duck after voice stops
    mute_during_speech: bool = False  # Fully mute instead of duck


class AudioDucker:
    """
    Audio ducking controller.
    
    Manages volume reduction during voice interactions:
    - When wake word is detected, duck audio
    - Hold duck while user is speaking
    - Fade back to normal after voice input ends
    
    Usage:
        ducker = AudioDucker()
        ducker.on_duck_change(lambda state, level: update_ui(level))
        await ducker.duck("wake_word")
        # ... user speaks ...
        await ducker.unduck()
    """
    
    def __init__(self, config: Optional[DuckingConfig] = None):
        """Initialize audio ducker with optional config."""
        self.config = config or DuckingConfig()
        self.state = DuckingState.NORMAL
        self.current_level = 1.0
        self.target_level = 1.0
        
        self._callbacks: List[Callable[[DuckingState, float], None]] = []
        self._fade_task: Optional[asyncio.Task] = None
        self._hold_task: Optional[asyncio.Task] = None
        self._duck_reason: Optional[str] = None
    
    def on_duck_change(self, callback: Callable[[DuckingState, float], None]) -> None:
        """Register callback for ducking state changes."""
        self._callbacks.append(callback)
    
    def _emit_change(self) -> None:
        """Emit ducking state change to callbacks."""
        for callback in self._callbacks:
            try:
                callback(self.state, self.current_level)
            except Exception as e:
                logger.error(f"Ducking callback error: {e}")
    
    async def duck(self, reason: str = "voice") -> None:
        """
        Start audio ducking.
        
        Args:
            reason: Why ducking is occurring (for logging/debugging)
        """
        if self.state == DuckingState.DUCKED:
            # Already ducked, just extend hold time
            if self._hold_task:
                self._hold_task.cancel()
            return
        
        self._duck_reason = reason
        logger.info(f"Audio ducking started: {reason}")
        
        # Cancel any existing fade
        if self._fade_task:
            self._fade_task.cancel()
        
        # Set target level
        if self.config.mute_during_speech:
            self.target_level = 0.0
            self.state = DuckingState.MUTED
        else:
            self.target_level = self.config.duck_level
            self.state = DuckingState.DUCKED
        
        # Start fade out (duck)
        self._fade_task = asyncio.create_task(
            self._fade_to(self.target_level, self.config.fade_out_ms)
        )
        await self._fade_task
    
    async def unduck(self) -> None:
        """
        End audio ducking with hold delay.
        
        Audio will fade back to normal after hold_time_ms.
        """
        if self.state == DuckingState.NORMAL:
            return
        
        # Cancel existing hold task
        if self._hold_task:
            self._hold_task.cancel()
        
        # Start hold timer
        self._hold_task = asyncio.create_task(self._hold_and_restore())
    
    async def unduck_immediate(self) -> None:
        """Immediately restore audio to normal volume."""
        if self._hold_task:
            self._hold_task.cancel()
        if self._fade_task:
            self._fade_task.cancel()
        
        self.target_level = 1.0
        self.state = DuckingState.NORMAL
        
        self._fade_task = asyncio.create_task(
            self._fade_to(1.0, self.config.fade_in_ms)
        )
        await self._fade_task
        
        logger.info(f"Audio ducking ended: {self._duck_reason}")
        self._duck_reason = None
    
    async def _hold_and_restore(self) -> None:
        """Hold duck then restore after delay."""
        try:
            await asyncio.sleep(self.config.hold_time_ms / 1000)
            await self.unduck_immediate()
        except asyncio.CancelledError:
            pass  # Hold was cancelled (voice continued)
    
    async def _fade_to(self, target: float, duration_ms: int) -> None:
        """
        Fade volume to target level.
        
        Args:
            target: Target volume level (0-1)
            duration_ms: Fade duration in milliseconds
        """
        steps = max(1, duration_ms // 20)  # 20ms per step
        step_duration = duration_ms / steps / 1000
        
        start_level = self.current_level
        level_change = target - start_level
        
        try:
            for i in range(steps):
                progress = (i + 1) / steps
                # Ease-out curve for natural feel
                eased_progress = 1 - (1 - progress) ** 2
                self.current_level = start_level + (level_change * eased_progress)
                self._emit_change()
                await asyncio.sleep(step_duration)
            
            self.current_level = target
            self._emit_change()
            
        except asyncio.CancelledError:
            pass  # Fade was interrupted
    
    def extend_duck(self) -> None:
        """
        Extend ducking hold time.
        
        Call this while user is still speaking to prevent unduck.
        """
        if self._hold_task:
            self._hold_task.cancel()
            self._hold_task = asyncio.create_task(self._hold_and_restore())
    
    def get_state(self) -> dict:
        """Get current ducking state."""
        return {
            "state": self.state.value,
            "level": self.current_level,
            "target_level": self.target_level,
            "reason": self._duck_reason
        }


# Singleton instance
_audio_ducker: Optional[AudioDucker] = None


def get_audio_ducker() -> AudioDucker:
    """Get the singleton audio ducker instance."""
    global _audio_ducker
    if _audio_ducker is None:
        _audio_ducker = AudioDucker()
    return _audio_ducker

