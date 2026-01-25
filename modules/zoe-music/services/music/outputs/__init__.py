"""
Music Output Targets Package
============================

Unified interface for audio playback destinations.
"""

from .base import OutputTarget, OutputState, OutputType
from .manager import OutputManager, get_output_manager

__all__ = [
    "OutputTarget",
    "OutputState",
    "OutputType",
    "OutputManager",
    "get_output_manager"
]

