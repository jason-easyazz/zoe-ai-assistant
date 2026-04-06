from .people import router as people_router
from .memories import router as memories_router
from .calendar import router as calendar_router
from .lists import router as lists_router
from .reminders import router as reminders_router
from .notes import router as notes_router
from .journal import router as journal_router
from .transactions import router as transactions_router
from .weather import router as weather_router
from .system import router as system_router
from .notifications import router as notifications_router
from .chat import router as chat_router
from .ui_actions import router as ui_router
from .voice_tts import router as voice_tts_router
from .user_profile import router as user_profile_router
from .panel_auth import router as panel_auth_router

__all__ = [
    "people_router",
    "memories_router",
    "calendar_router",
    "lists_router",
    "reminders_router",
    "notes_router",
    "journal_router",
    "transactions_router",
    "weather_router",
    "system_router",
    "notifications_router",
    "chat_router",
    "ui_router",
    "voice_tts_router",
    "user_profile_router",
    "panel_auth_router",
]
