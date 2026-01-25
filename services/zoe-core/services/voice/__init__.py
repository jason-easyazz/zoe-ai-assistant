"""
Voice Services Package
======================

Wake word detection, audio ducking, and voice control integration.
"""

from .wake_word import WakeWordDetector, get_wake_word_detector
from .audio_ducking import AudioDucker, get_audio_ducker
from .barge_in import BargeInController, get_barge_in_controller

__all__ = [
    "WakeWordDetector",
    "get_wake_word_detector",
    "AudioDucker",
    "get_audio_ducker",
    "BargeInController",
    "get_barge_in_controller"
]

