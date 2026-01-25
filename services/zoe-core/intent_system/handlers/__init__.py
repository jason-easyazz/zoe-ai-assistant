"""
Intent Handlers
===============

Domain-specific intent handlers that execute actions.
"""

from . import lists_handlers
from . import calendar_handlers
from . import weather_handlers
from . import time_handlers
from . import greeting_handlers
from . import homeassistant_handlers
# music_handlers now loaded from modules via auto-discovery

__all__ = [
    "lists_handlers",
    "calendar_handlers", 
    "weather_handlers",
    "time_handlers",
    "greeting_handlers",
    "homeassistant_handlers",
]

