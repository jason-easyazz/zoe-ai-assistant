"""
MCP Server for zoe-data.
Exposes family data tools that OpenClaw skills can call.
Runs as a stdio MCP server alongside the REST API.
"""
import asyncio
import json
import random
import sys
import uuid
import aiosqlite
import httpx
from datetime import date, datetime, timedelta
import os

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ZOE_DATA_DB", os.path.join(_BASE_DIR, "zoe.db"))
OPENWEATHERMAP_API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY", "")
_BROADCAST_URL = "http://127.0.0.1:8000/api/internal/broadcast"
_OPENCLAW_GW = os.environ.get("ZOE_OPENCLAW_GW", "http://127.0.0.1:18789")
# When true (rollout goal), tools/call without _user_id/user_id is rejected.
# Until OpenClaw skills are caught up, default false logs a warning and falls
# back to family-admin so existing workflows don't break silently.
_MCP_STRICT_USER_ID = os.environ.get("ZOE_MCP_STRICT_USER_ID", "false").strip().lower() == "true"

import logging as _mcp_logging
_mcp_log = _mcp_logging.getLogger("mcp_server")
from browser_broker import create_default_browser_broker

_BROWSER_BROKER = create_default_browser_broker(_OPENCLAW_GW)


async def _notify_ui(channel: str, event_type: str, data: dict):
    """Fire-and-forget broadcast to connected UI clients via the FastAPI app."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(_BROADCAST_URL, json={
                "channel": channel,
                "event_type": event_type,
                "data": data,
            })
    except Exception:
        pass


async def _get_weather_default_location(db) -> dict:
    """System-level weather fallback location (admin-configured or env default)."""
    try:
        cursor = await db.execute(
            "SELECT value FROM system_preferences WHERE key = ?",
            ("weather_default_location",),
        )
        row = await cursor.fetchone()
        if row and row["value"]:
            payload = json.loads(row["value"])
            if isinstance(payload, dict):
                lat = payload.get("latitude")
                lon = payload.get("longitude")
                if lat is not None and lon is not None:
                    return {
                        "latitude": float(lat),
                        "longitude": float(lon),
                        "city": payload.get("city") or "Geraldton",
                        "timezone": payload.get("timezone") or os.environ.get("ZOE_TIMEZONE", "Australia/Perth"),
                    }
    except Exception:
        pass
    return {
        "latitude": float(os.environ.get("ZOE_LOCATION_LAT", "-28.7774")),
        "longitude": float(os.environ.get("ZOE_LOCATION_LON", "114.6158")),
        "city": os.environ.get("ZOE_LOCATION_CITY", "Geraldton"),
        "timezone": os.environ.get("ZOE_TIMEZONE", "Australia/Perth"),
    }

TOOLS = [
    {
        "name": "calendar_list_events",
        "description": "List calendar events. Optionally filter by date range or category.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "category": {"type": "string", "description": "Event category filter"},
            },
        },
    },
    {
        "name": "calendar_create_event",
        "description": "Create a new calendar event.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "start_time": {"type": "string", "description": "Start time (HH:MM)"},
                "end_time": {"type": "string", "description": "End time (HH:MM)"},
                "category": {"type": "string", "description": "Category"},
                "location": {"type": "string", "description": "Location"},
                "all_day": {"type": "boolean", "description": "All day event"},
            },
            "required": ["title", "start_date"],
        },
    },
    {
        "name": "calendar_today",
        "description": "Get today's calendar events.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_get_items",
        "description": "Get items from a list. Specify list_type (shopping, personal, work, tasks) and optionally list_name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "list_type": {"type": "string", "description": "Type: shopping, personal, work, tasks, bucket"},
                "list_name": {"type": "string", "description": "Specific list name to find"},
            },
            "required": ["list_type"],
        },
    },
    {
        "name": "list_add_item",
        "description": "Add an item to a list. Creates the list if it doesn't exist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "list_type": {"type": "string", "description": "Type: shopping, personal, work, tasks"},
                "list_name": {"type": "string", "description": "List name (creates if not exists)"},
                "text": {"type": "string", "description": "Item text"},
                "quantity": {"type": "string", "description": "Quantity (e.g. '2L', '500g')"},
                "category": {"type": "string", "description": "Item category (e.g. 'dairy', 'produce')"},
            },
            "required": ["list_type", "text"],
        },
    },
    {
        "name": "list_remove_item",
        "description": "Remove/check off an item from a list by marking it complete.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "list_type": {"type": "string", "description": "List type"},
                "item_text": {"type": "string", "description": "Item text to find and remove"},
            },
            "required": ["list_type", "item_text"],
        },
    },
    {
        "name": "reminder_create",
        "description": "Set a reminder for someone.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Reminder title"},
                "due_date": {"type": "string", "description": "Due date (YYYY-MM-DD)"},
                "due_time": {"type": "string", "description": "Due time (HH:MM)"},
                "priority": {"type": "string", "description": "Priority: low, normal, high, urgent"},
                "category": {"type": "string", "description": "Category"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "reminder_list",
        "description": "List active reminders, optionally filtered by date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "today_only": {"type": "boolean", "description": "Only show today's reminders"},
            },
        },
    },
    {
        "name": "people_search",
        "description": "Search for a person in the family contacts by name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name or keyword to search"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "people_create",
        "description": "Add a new person to family contacts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Person's name"},
                "relationship": {"type": "string", "description": "Relationship (friend, family, colleague, etc.)"},
                "birthday": {"type": "string", "description": "Birthday (YYYY-MM-DD)"},
                "phone": {"type": "string", "description": "Phone number"},
                "email": {"type": "string", "description": "Email address"},
                "notes": {"type": "string", "description": "Notes about this person"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "note_create",
        "description": "Save a note.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title"},
                "content": {"type": "string", "description": "Note content"},
                "category": {"type": "string", "description": "Category"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "note_search",
        "description": "Search notes by keyword.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "dashboard_get_layout",
        "description": "Get a user's dashboard widget layout.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User ID (default: current user)"},
            },
        },
    },
    {
        "name": "dashboard_save_layout",
        "description": "Save a user's dashboard widget layout.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User ID"},
                "layout": {"type": "array", "description": "Array of widget configs with id, x, y, w, h"},
            },
            "required": ["layout"],
        },
    },
    {
        "name": "dashboard_add_widget",
        "description": "Add widget(s) to a user's dashboard. Widgets: weather, events, tasks, shopping, notes, reminders, calendar, people, journal, home, time, zoe-orb, week-planner, personal, work, bucket.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "widgets": {"type": "array", "items": {"type": "string"}, "description": "Widget IDs to add"},
            },
            "required": ["widgets"],
        },
    },
    {
        "name": "dashboard_available_widgets",
        "description": "List all available dashboard widgets.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # --- Weather tools ---
    {
        "name": "weather_current",
        "description": "Get current weather for the user's saved location.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name override (optional, uses saved prefs by default)"},
            },
        },
    },
    {
        "name": "weather_forecast",
        "description": "Get weather forecast for the next few days.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name override"},
                "days": {"type": "integer", "description": "Number of forecast periods (default 5)"},
            },
        },
    },
    # --- Self-awareness tools (on-demand, zero per-message cost) ---
    {
        "name": "zoe_get_time",
        "description": "Get the current date and time in the configured timezone.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "zoe_get_status",
        "description": "Get Zoe's current system status: active agents, uptime, memory store info.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "zoe_list_skills",
        "description": "List the Zoe skills and capabilities currently available.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "zoe_self_capabilities",
        "description": (
            "Authoritative live snapshot of what Zoe is and can do: running "
            "services, active agents, installed widgets, available pages, "
            "OpenClaw skills, and whether she can build new widgets/pages/"
            "capabilities (can_build). Call this BEFORE telling a user Zoe "
            "can't do something."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    # --- Journal tools ---
    {
        "name": "journal_create_entry",
        "description": "Create a new journal entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Journal entry content"},
                "title": {"type": "string", "description": "Entry title"},
                "mood": {"type": "string", "description": "Mood (happy, sad, anxious, calm, excited, grateful, neutral)"},
                "mood_score": {"type": "integer", "description": "Mood score 1-10"},
                "tags": {"type": "string", "description": "Comma-separated tags"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "journal_list_entries",
        "description": "List journal entries with optional filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max entries to return (default 10)"},
                "start_date": {"type": "string", "description": "Filter from date (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "Filter to date (YYYY-MM-DD)"},
                "mood": {"type": "string", "description": "Filter by mood"},
                "search": {"type": "string", "description": "Search in title and content"},
            },
        },
    },
    {
        "name": "journal_get_streak",
        "description": "Get journaling streak stats: current streak, longest streak, total entries.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "journal_get_prompts",
        "description": "Get 5 random journaling prompts to inspire writing.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "journal_on_this_day",
        "description": "Get journal entries from this day in previous years.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # --- Transaction tools ---
    {
        "name": "transaction_create",
        "description": "Record a financial transaction (expense or income).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What was purchased or earned"},
                "amount": {"type": "number", "description": "Amount in dollars"},
                "type": {"type": "string", "description": "Type: expense or income (default expense)"},
                "transaction_date": {"type": "string", "description": "Date (YYYY-MM-DD, default today)"},
                "category": {"type": "string", "description": "Category (groceries, dining, transport, utilities, entertainment, health, etc.)"},
                "payment_method": {"type": "string", "description": "Payment method (cash, card, transfer)"},
            },
            "required": ["description", "amount"],
        },
    },
    {
        "name": "transaction_list",
        "description": "List recent transactions with optional filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "From date (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "To date (YYYY-MM-DD)"},
                "type": {"type": "string", "description": "Filter: expense or income"},
                "category": {"type": "string", "description": "Filter by category"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
        },
    },
    {
        "name": "transaction_summary",
        "description": "Get spending summary for a period.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "Period: week or month (default week)"},
            },
        },
    },
    # --- Calendar CRUD ---
    {
        "name": "calendar_update_event",
        "description": "Update an existing calendar event.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID to update"},
                "title": {"type": "string", "description": "New title"},
                "start_date": {"type": "string", "description": "New start date (YYYY-MM-DD)"},
                "start_time": {"type": "string", "description": "New start time (HH:MM)"},
                "end_time": {"type": "string", "description": "New end time (HH:MM)"},
                "location": {"type": "string", "description": "New location"},
                "category": {"type": "string", "description": "New category"},
                "all_day": {"type": "boolean", "description": "Set as all-day event"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_delete_event",
        "description": "Delete a calendar event (soft delete).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID to delete"},
            },
            "required": ["event_id"],
        },
    },
    # --- Reminder CRUD ---
    {
        "name": "reminder_update",
        "description": "Update an existing reminder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "Reminder ID to update"},
                "title": {"type": "string", "description": "New title"},
                "due_date": {"type": "string", "description": "New due date (YYYY-MM-DD)"},
                "due_time": {"type": "string", "description": "New due time (HH:MM)"},
                "priority": {"type": "string", "description": "New priority"},
            },
            "required": ["reminder_id"],
        },
    },
    {
        "name": "reminder_delete",
        "description": "Delete a reminder (soft delete).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "Reminder ID to delete"},
            },
            "required": ["reminder_id"],
        },
    },
    {
        "name": "reminder_snooze",
        "description": "Snooze a reminder for a specified number of minutes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "Reminder ID to snooze"},
                "minutes": {"type": "integer", "description": "Minutes to snooze (default 30)"},
            },
            "required": ["reminder_id"],
        },
    },
    # --- Note CRUD ---
    {
        "name": "note_update",
        "description": "Update an existing note.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "note_id": {"type": "string", "description": "Note ID to update"},
                "title": {"type": "string", "description": "New title"},
                "content": {"type": "string", "description": "New content"},
                "category": {"type": "string", "description": "New category"},
            },
            "required": ["note_id"],
        },
    },
    {
        "name": "note_delete",
        "description": "Delete a note (soft delete).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "note_id": {"type": "string", "description": "Note ID to delete"},
            },
            "required": ["note_id"],
        },
    },
    # --- People CRUD ---
    {
        "name": "people_update",
        "description": "Update an existing person's details.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "person_id": {"type": "string", "description": "Person ID to update"},
                "name": {"type": "string", "description": "New name"},
                "relationship": {"type": "string", "description": "New relationship"},
                "birthday": {"type": "string", "description": "Birthday (YYYY-MM-DD)"},
                "phone": {"type": "string", "description": "Phone number"},
                "email": {"type": "string", "description": "Email address"},
                "notes": {"type": "string", "description": "Notes about this person"},
            },
            "required": ["person_id"],
        },
    },
    {
        "name": "people_delete",
        "description": "Delete a person from contacts (soft delete).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "person_id": {"type": "string", "description": "Person ID to delete"},
            },
            "required": ["person_id"],
        },
    },
    # --- Notification tool ---
    {
        "name": "notification_create",
        "description": "Create a notification for the user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Notification title"},
                "message": {"type": "string", "description": "Notification message"},
                "type": {"type": "string", "description": "Type: info, warning, success, reminder (default info)"},
                "priority": {"type": "string", "description": "Priority: low, normal, high (default normal)"},
            },
            "required": ["title", "message"],
        },
    },
    # --- Touch Presence Platform panel controls ---
    {
        "name": "panel_navigate",
        "description": (
            "Load a URL full-screen on a touch panel (e.g. the Raspberry Pi kiosk). "
            "Use this to show the user what you are doing: a web search result, a Home Assistant "
            "dashboard (/ha/lovelace/default_view), or any URL. "
            "Example: panel_navigate(url='https://www.google.com/search?q=weather', panel_id='zoe-touch-pi')"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to load on the panel (must be http/https)"},
                "panel_id": {"type": "string", "description": "Target panel ID (default: foreground panel)"},
                "label": {"type": "string", "description": "Optional label shown briefly on the panel"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "panel_clear",
        "description": "Return the touch panel to its ambient dashboard view.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "panel_id": {"type": "string", "description": "Target panel ID (default: foreground panel)"},
            },
        },
    },
    {
        "name": "panel_show_fullscreen",
        "description": "Display a base64 PNG image full-screen on the touch panel (e.g. a browser screenshot).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "image_base64": {"type": "string", "description": "Base64-encoded PNG image to display"},
                "caption": {"type": "string", "description": "Optional caption shown at the bottom"},
                "panel_id": {"type": "string", "description": "Target panel ID (default: foreground panel)"},
            },
            "required": ["image_base64"],
        },
    },
    {
        "name": "panel_browser_screenshot",
        "description": (
            "Capture a screenshot of the current page in OpenClaw's automation browser and display it "
            "full-screen on the touch panel. Use this to show the user what Zoe is doing in the browser "
            "(e.g. during a web search, HA setup, or login flow). "
            "Optionally navigates to a URL first. Returns the panel action result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "caption": {"type": "string", "description": "Caption shown under the screenshot on the panel"},
                "panel_id": {"type": "string", "description": "Target panel ID (default: foreground panel)"},
                "navigate_to": {"type": "string", "description": "Optional URL to navigate to before screenshotting"},
            },
        },
    },
    {
        "name": "browser_get_capabilities",
        "description": "Return browser backend capability matrix and availability.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_compare_backends",
        "description": "Return side-by-side backend comparison and active recommendation.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "panel_announce",
        "description": "Play a TTS announcement on a panel (or all panels).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Text to speak aloud"},
                "panel_id": {"type": "string", "description": "Target panel ID, or 'all' for all panels"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "panel_request_auth",
        "description": (
            "Ask the user to enter their PIN on the touch panel to authorise a high-privilege action. "
            "Returns a challenge_id. Poll panel_check_auth to confirm approval before proceeding."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "panel_id": {"type": "string", "description": "Panel to show the PIN pad on"},
                "action_context": {"type": "string", "description": "Short description shown to the user"},
            },
            "required": ["panel_id", "action_context"],
        },
    },
    {
        "name": "panel_check_auth",
        "description": "Check the status of a PIN auth challenge (pending / approved / rejected / expired).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "challenge_id": {"type": "string", "description": "Challenge ID from panel_request_auth"},
            },
            "required": ["challenge_id"],
        },
    },
    {
        "name": "panel_set_mode",
        "description": "Set the touch panel display mode: ambient | fullscreen | listening | thinking | responding | overlay.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["ambient", "fullscreen", "listening", "thinking", "responding", "overlay"]},
                "panel_id": {"type": "string", "description": "Target panel ID (default: foreground panel)"},
            },
            "required": ["mode"],
        },
    },
    {
        "name": "panel_show_smart_home",
        "description": "Show a smart home control overlay on the touch panel. Displays entity states and toggles.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "description": "List of entity objects: [{entity_id, name, state, icon}]",
                    "items": {"type": "object"},
                },
                "title": {"type": "string", "description": "Overlay title"},
                "dismiss_after": {"type": "integer", "description": "Auto-dismiss after N seconds (default 30)"},
                "panel_id": {"type": "string", "description": "Target panel ID (default: foreground panel)"},
            },
        },
    },
    {
        "name": "panel_show_media",
        "description": "Show a now-playing media card on the touch panel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Track or content title"},
                "artist": {"type": "string", "description": "Artist or show name"},
                "album_art": {"type": "string", "description": "Album art URL"},
                "entity_id": {"type": "string", "description": "HA media_player entity_id for transport controls"},
                "dismiss_after": {"type": "integer", "description": "Auto-dismiss after N seconds (default 20)"},
                "panel_id": {"type": "string", "description": "Target panel ID (default: foreground panel)"},
            },
        },
    },
    {
        "name": "media_get_now_playing",
        "description": "Get the currently playing media from Home Assistant media player entities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Specific media_player entity (optional, fetches all if omitted)"},
            },
        },
    },
    {
        "name": "ambient_search",
        "description": (
            "Search ambient memory transcripts captured by the always-on microphone. "
            "Use to answer questions like 'What did I talk to Brad about?' or "
            "'What was mentioned in the kitchen yesterday?'. "
            "Searches full-text across all stored ambient transcripts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Full-text search query (e.g. 'Brad meeting', 'grocery list')",
                },
                "room": {
                    "type": "string",
                    "description": "Filter by room/location (e.g. 'kitchen', 'living room')",
                },
                "speaker_id": {
                    "type": "string",
                    "description": "Filter by identified speaker user_id",
                },
                "date_from": {
                    "type": "string",
                    "description": "Filter transcripts after this date (ISO 8601, e.g. '2026-04-01')",
                },
                "date_to": {
                    "type": "string",
                    "description": "Filter transcripts before this date (ISO 8601)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of results to return (default 10, max 50)",
                },
            },
            "required": ["query"],
        },
    },
    # === MEMORY TOOLS (MemPalace via MemoryService) ============================
    {
        "name": "memory_add",
        "description": (
            "Store a persistent fact about the user in MemPalace. Use for "
            "preferences, identity, relationships, health, habits, plans, and "
            "other facts that should survive across sessions. Automatically "
            "scrubbed for PII (credit cards, SSN, 2FA codes, passwords)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Short third-person fact (≤300 chars)."},
                "memory_type": {
                    "type": "string",
                    "description": "fact | preference | identity | relationship | habit | goal | health | plan | temporal",
                },
                "confidence": {"type": "number", "description": "0.0–1.0 confidence (default 0.85)"},
                "entity_type": {"type": "string", "description": "Optional: self | person | place | thing"},
                "entity_id": {"type": "string", "description": "Optional: person id, place id, …"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "expires_at": {"type": "string", "description": "Optional ISO-8601 expiry"},
                "status": {
                    "type": "string",
                    "description": "approved | pending (default approved for MCP writes)",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "memory_search",
        "description": (
            "Semantic search across the calling user's MemPalace rows. Returns "
            "the top-k matches ranked by relevance (and access recency)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language query."},
                "limit": {"type": "integer", "description": "Max matches (default 8, cap 50)."},
                "memory_type": {"type": "string", "description": "Optional type filter (applied client-side)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_list",
        "description": (
            "List memories by status for the calling user. Use to inspect the "
            "review queue (status='pending'), or to audit approved rows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "pending | approved | rejected | superseded | archived (default pending)",
                },
                "limit": {"type": "integer", "description": "Max rows (default 25, cap 200)."},
            },
        },
    },
    {
        "name": "memory_review",
        "description": (
            "Approve / reject / edit a single MemPalace row. Edits create a "
            "new row that supersedes the original. All reviews are audited. "
            "Requires ownership unless caller is an admin."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "decision": {"type": "string", "description": "approve | reject | edit"},
                "edits": {"type": "string", "description": "Required when decision='edit'."},
                "note": {"type": "string", "description": "Reason / audit note."},
            },
            "required": ["memory_id", "decision"],
        },
    },
    {
        "name": "memory_forget",
        "description": (
            "Soft-delete a specific memory by id (marks it archived and writes "
            "an audit row). Owner or admin only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "note": {"type": "string", "description": "Optional audit reason."},
            },
            "required": ["memory_id"],
        },
    },
]


async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def handle_tool(name: str, args: dict) -> str:
    db = await get_db()
    try:
        result = await _execute_tool(db, name, args)
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        await db.close()


_LIST_TYPE_ALIASES = {
    "personal_todos": "personal",
    "work_todos": "work",
    "grocery": "shopping",
    "groceries": "shopping",
    "todo": "tasks",
    "todos": "tasks",
}


async def _resolve_panel_owner(db, panel_id, fallback_user_id: str):
    """Map panel_id to the user_id bound in ui_panel_sessions so /api/ui/actions/pending matches the kiosk."""
    if panel_id and str(panel_id) != "all":
        cur = await db.execute(
            "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY updated_at DESC LIMIT 1",
            (str(panel_id),),
        )
        row = await cur.fetchone()
        if row:
            return row["user_id"], str(panel_id)
    cur2 = await db.execute(
        """SELECT user_id, panel_id FROM ui_panel_sessions
           WHERE is_foreground = 1 ORDER BY updated_at DESC LIMIT 1"""
    )
    row2 = await cur2.fetchone()
    if row2:
        return row2["user_id"], row2["panel_id"]
    return fallback_user_id, panel_id


async def _enqueue_panel_tool(db, *, user_id_fallback: str, panel_id, action_type: str, payload: dict):
    """Queue a touch-panel action in ui_actions (and WS push) for the touch-ui-executor poll loop."""
    from ui_orchestrator import enqueue_ui_action

    if panel_id == "all":
        cur = await db.execute(
            "SELECT user_id, panel_id FROM ui_panel_sessions GROUP BY panel_id"
        )
        rows = await cur.fetchall()
        if not rows:
            uid, pid = await _resolve_panel_owner(db, None, user_id_fallback)
            return await enqueue_ui_action(
                db,
                user_id=uid,
                action_type=action_type,
                payload=payload,
                requested_by="openclaw",
                panel_id=pid,
            )
        last = None
        for r in rows:
            last = await enqueue_ui_action(
                db,
                user_id=r["user_id"],
                action_type=action_type,
                payload=payload,
                requested_by="openclaw",
                panel_id=r["panel_id"],
            )
        return last
    uid, pid = await _resolve_panel_owner(db, panel_id, user_id_fallback)
    return await enqueue_ui_action(
        db,
        user_id=uid,
        action_type=action_type,
        payload=payload,
        requested_by="openclaw",
        panel_id=pid,
    )


class _MissingUserId(Exception):
    """Raised when strict mode rejects a tool call with no identity."""


async def _execute_tool(db, name: str, args: dict):
    _uid_raw = args.pop("_user_id", args.pop("user_id", None))
    if _uid_raw is None:
        if _MCP_STRICT_USER_ID:
            raise _MissingUserId(
                f"tool '{name}' requires explicit _user_id (strict mode enabled)"
            )
        _mcp_log.warning(
            "mcp tool '%s' called without user_id — falling back to family-admin. "
            "Set ZOE_MCP_STRICT_USER_ID=true once all callers pass _user_id.",
            name,
        )
        user_id = "family-admin"
    else:
        user_id = _uid_raw

    if "list_type" in args:
        args["list_type"] = _LIST_TYPE_ALIASES.get(args["list_type"], args["list_type"])

    if name == "calendar_list_events":
        sql = "SELECT id, title, start_date, start_time, end_time, category, location, all_day FROM events WHERE deleted=0"
        params = []
        if args.get("start_date"):
            sql += " AND start_date >= ?"
            params.append(args["start_date"])
        if args.get("end_date"):
            sql += " AND start_date <= ?"
            params.append(args["end_date"])
        if args.get("category"):
            sql += " AND category = ?"
            params.append(args["category"])
        sql += " ORDER BY start_date, start_time LIMIT 20"
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return {"events": [dict(r) for r in rows]}

    elif name == "calendar_create_event":
        eid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO events (id, user_id, title, start_date, start_time, end_time, category, location, all_day, visibility) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid, user_id, args["title"], args["start_date"], args.get("start_time"),
             args.get("end_time"), args.get("category", "general"), args.get("location"),
             1 if args.get("all_day") else 0, "family"),
        )
        await db.commit()
        result = {"id": eid, "title": args["title"], "start_date": args["start_date"],
                  "start_time": args.get("start_time"), "category": args.get("category", "general")}
        await _notify_ui("calendar", "event_created", result)
        return {**result, "date": args["start_date"], "status": "created"}

    elif name == "calendar_today":
        today = date.today().isoformat()
        cursor = await db.execute(
            "SELECT id, title, start_time, end_time, category, location FROM events WHERE start_date=? AND deleted=0 ORDER BY start_time",
            (today,),
        )
        rows = await cursor.fetchall()
        return {"date": today, "events": [dict(r) for r in rows]}

    elif name == "list_get_items":
        lt = args["list_type"]
        ln = args.get("list_name")
        if ln:
            cursor = await db.execute(
                "SELECT l.id, l.name, li.id as item_id, li.text, li.completed, li.quantity, li.category FROM lists l LEFT JOIN list_items li ON l.id = li.list_id AND li.deleted=0 WHERE l.user_id=? AND l.list_type=? AND l.name LIKE ? AND l.deleted=0 ORDER BY li.sort_order",
                (user_id, lt, f"%{ln}%"),
            )
        else:
            cursor = await db.execute(
                "SELECT l.id, l.name, li.id as item_id, li.text, li.completed, li.quantity, li.category FROM lists l LEFT JOIN list_items li ON l.id = li.list_id AND li.deleted=0 WHERE l.user_id=? AND l.list_type=? AND l.deleted=0 ORDER BY l.name, li.sort_order",
                (user_id, lt),
            )
        rows = await cursor.fetchall()
        lists_map = {}
        for r in rows:
            d = dict(r)
            lid = d["id"]
            if lid not in lists_map:
                lists_map[lid] = {"id": lid, "name": d["name"], "items": []}
            if d.get("item_id") and d.get("text"):
                lists_map[lid]["items"].append({
                    "id": d["item_id"], "text": d["text"],
                    "completed": bool(d["completed"]),
                    "quantity": d.get("quantity"), "category": d.get("category"),
                })
        return {"lists": list(lists_map.values())}

    elif name == "list_add_item":
        lt = args["list_type"]
        ln = args.get("list_name", lt.capitalize())
        cursor = await db.execute(
            "SELECT id FROM lists WHERE user_id=? AND list_type=? AND name=? AND deleted=0 LIMIT 1",
            (user_id, lt, ln),
        )
        row = await cursor.fetchone()
        if row:
            list_id = row["id"]
        else:
            list_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO lists (id, user_id, name, list_type, visibility) VALUES (?,?,?,?,?)",
                (list_id, user_id, ln, lt, "family"),
            )
        item_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO list_items (id, list_id, text, quantity, category) VALUES (?,?,?,?,?)",
            (item_id, list_id, args["text"], args.get("quantity"), args.get("category")),
        )
        await db.commit()
        result = {"item_id": item_id, "list": ln, "list_id": list_id, "text": args["text"], "status": "added"}
        await _notify_ui("lists", "list_updated", {"action": "item_added", "list_id": list_id, "item": {"id": item_id, "text": args["text"]}})
        return result

    elif name == "list_remove_item":
        lt = args["list_type"]
        text = args["item_text"]
        cursor = await db.execute(
            "SELECT li.id, li.list_id FROM list_items li JOIN lists l ON li.list_id = l.id WHERE l.user_id=? AND l.list_type=? AND li.text LIKE ? AND li.deleted=0 AND l.deleted=0 LIMIT 1",
            (user_id, lt, f"%{text}%"),
        )
        row = await cursor.fetchone()
        if not row:
            return {"error": f"Item '{text}' not found in {lt} lists"}
        item_id = row["id"]
        await db.execute("UPDATE list_items SET completed=1, updated_at=datetime('now') WHERE id=?", (item_id,))
        await db.commit()
        await _notify_ui("lists", "list_updated", {"action": "item_completed", "list_id": row["list_id"], "item_id": item_id})
        return {"item_id": item_id, "text": text, "status": "completed"}

    elif name == "reminder_create":
        rid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO reminders (id, user_id, title, due_date, due_time, priority, category, visibility) VALUES (?,?,?,?,?,?,?,?)",
            (rid, user_id, args["title"], args.get("due_date"), args.get("due_time"),
             args.get("priority", "normal"), args.get("category", "general"), "personal"),
        )
        await db.commit()
        result = {"id": rid, "title": args["title"], "due_date": args.get("due_date"),
                  "due_time": args.get("due_time"), "priority": args.get("priority", "normal")}
        await _notify_ui("reminders", "reminder_created", result)
        return {**result, "status": "created"}

    elif name == "reminder_list":
        if args.get("today_only"):
            today = date.today().isoformat()
            cursor = await db.execute(
                "SELECT id, title, due_date, due_time, priority, category FROM reminders WHERE due_date=? AND is_active=1 AND deleted=0 AND user_id=? ORDER BY due_time",
                (today, user_id),
            )
        else:
            cursor = await db.execute(
                "SELECT id, title, due_date, due_time, priority, category FROM reminders WHERE is_active=1 AND deleted=0 AND user_id=? ORDER BY due_date, due_time LIMIT 20",
                (user_id,),
            )
        rows = await cursor.fetchall()
        return {"reminders": [dict(r) for r in rows]}

    elif name == "people_search":
        q = args["query"]
        cursor = await db.execute(
            "SELECT id, name, relationship, birthday, phone, email FROM people WHERE name LIKE ? AND deleted=0 LIMIT 10",
            (f"%{q}%",),
        )
        rows = await cursor.fetchall()
        return {"people": [dict(r) for r in rows]}

    elif name == "people_create":
        pid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO people (id, user_id, name, relationship, birthday, phone, email, notes, visibility) VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, user_id, args["name"], args.get("relationship"), args.get("birthday"),
             args.get("phone"), args.get("email"), args.get("notes"), "family"),
        )
        await db.commit()
        result = {"id": pid, "name": args["name"], "relationship": args.get("relationship")}
        await _notify_ui("all", "people:created", result)
        # Mirror to MemPalace so OpenClaw-authored contacts show up in
        # memory retrieval identically to HTTP-router-authored ones.
        try:
            from routers.people import _store_person_memory  # type: ignore
            await _store_person_memory(
                db, user_id,
                {**result, "birthday": args.get("birthday"), "phone": args.get("phone"),
                 "email": args.get("email"), "notes": args.get("notes")},
                "created",
            )
        except Exception as exc:
            _mcp_log.info("mcp people_create memory mirror skipped: %s", exc)
        return {**result, "status": "created"}

    elif name == "note_create":
        nid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO notes (id, user_id, title, content, category, visibility) VALUES (?,?,?,?,?,?)",
            (nid, user_id, args.get("title"), args["content"], args.get("category", "general"), "personal"),
        )
        await db.commit()
        result = {"id": nid, "title": args.get("title"), "category": args.get("category", "general")}
        await _notify_ui("notes", "note_created", result)
        try:
            from routers.notes import _store_note_memory  # type: ignore
            await _store_note_memory(
                db, user_id,
                {**result, "content": args["content"]},
                "created",
            )
        except Exception as exc:
            _mcp_log.info("mcp note_create memory mirror skipped: %s", exc)
        return {**result, "status": "created"}

    elif name == "note_search":
        q = args["query"]
        cursor = await db.execute(
            "SELECT id, title, content, category, created_at FROM notes WHERE (title LIKE ? OR content LIKE ?) AND deleted=0 LIMIT 10",
            (f"%{q}%", f"%{q}%"),
        )
        rows = await cursor.fetchall()
        return {"notes": [dict(r) for r in rows]}

    elif name == "dashboard_get_layout":
        uid = args.get("user_id", user_id)
        cursor = await db.execute(
            "SELECT layout, updated_at FROM dashboard_layouts WHERE user_id = ?",
            (uid,),
        )
        row = await cursor.fetchone()
        if row:
            return {"layout": json.loads(row["layout"]), "updated_at": row["updated_at"]}
        return {"layout": None, "message": "No layout saved yet"}

    elif name == "dashboard_save_layout":
        uid = args.get("user_id", user_id)
        layout_payload = json.dumps(args.get("layout", []))
        await db.execute(
            "INSERT INTO dashboard_layouts (user_id, layout, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(user_id) DO UPDATE SET layout = excluded.layout, updated_at = datetime('now')",
            (uid, layout_payload),
        )
        await db.commit()
        return {"status": "ok"}

    elif name == "dashboard_add_widget":
        uid = args.get("user_id", user_id)
        widget_ids = args.get("widgets", [])
        VALID_WIDGETS = {
            "weather",
            "events",
            "tasks",
            "shopping",
            "notes",
            "reminders",
            "calendar",
            "people",
            "journal",
            "home",
            "time",
            "zoe-orb",
            "week-planner",
            "personal",
            "work",
            "bucket",
        }
        to_add = [w for w in widget_ids if w in VALID_WIDGETS]
        if not to_add:
            return {"status": "error", "message": "No valid widget IDs"}
        cursor = await db.execute(
            "SELECT layout FROM dashboard_layouts WHERE user_id = ?",
            (uid,),
        )
        row = await cursor.fetchone()
        current = json.loads(row["layout"]) if row else []
        existing = {w.get("id") for w in current if isinstance(w, dict)}
        max_y = max((w.get("y", 0) + w.get("h", 2) for w in current), default=0)
        added = []
        for wid in to_add:
            if wid in existing:
                continue
            current.append({"id": wid, "x": 0, "y": max_y, "w": 2, "h": 2})
            max_y += 2
            added.append(wid)
        await db.execute(
            "INSERT INTO dashboard_layouts (user_id, layout, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(user_id) DO UPDATE SET layout = excluded.layout, updated_at = datetime('now')",
            (uid, json.dumps(current)),
        )
        await db.commit()
        return {"status": "ok", "added": added}

    elif name == "dashboard_available_widgets":
        widgets = [
            {"id": "weather", "name": "Weather", "icon": "sun"},
            {"id": "events", "name": "Calendar Events", "icon": "calendar"},
            {"id": "tasks", "name": "Tasks", "icon": "check"},
            {"id": "shopping", "name": "Shopping List", "icon": "cart"},
            {"id": "notes", "name": "Quick Notes", "icon": "note"},
            {"id": "reminders", "name": "Reminders", "icon": "bell"},
            {"id": "calendar", "name": "Calendar Grid", "icon": "grid"},
            {"id": "people", "name": "People", "icon": "users"},
            {"id": "journal", "name": "Journal", "icon": "book"},
            {"id": "home", "name": "Home Control", "icon": "home"},
            {"id": "time", "name": "Clock", "icon": "clock"},
            {"id": "zoe-orb", "name": "Zoe Orb", "icon": "orb"},
            {"id": "week-planner", "name": "Week Planner", "icon": "planner"},
            {"id": "personal", "name": "Personal Todos", "icon": "pin"},
            {"id": "work", "name": "Work Todos", "icon": "briefcase"},
            {"id": "bucket", "name": "Bucket List", "icon": "star"},
        ]
        return {"widgets": widgets}

    # === WEATHER TOOLS (Open-Meteo — free, no API key) ===
    elif name == "weather_current":
        # WMO weather code → human-readable description
        _WMO = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                45: "Fog", 48: "Icy fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
                61: "Slight rain", 63: "Rain", 65: "Heavy rain", 71: "Slight snow", 73: "Snow",
                75: "Heavy snow", 80: "Rain showers", 81: "Rain showers", 82: "Violent showers",
                85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
                96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail"}
        city = args.get("city")
        lat = lon = None
        if not city:
            cursor = await db.execute(
                "SELECT latitude, longitude, city FROM weather_preferences WHERE user_id=?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row:
                d = dict(row)
                lat, lon, city = d.get("latitude"), d.get("longitude"), d.get("city")
        # Fallback to system-level default (admin setting), then env defaults.
        if not lat:
            default_loc = await _get_weather_default_location(db)
            lat = default_loc["latitude"]
            lon = default_loc["longitude"]
            city = city or default_loc["city"]
            tz = default_loc["timezone"]
        else:
            city = city or os.environ.get("ZOE_LOCATION_CITY", "Geraldton")
            tz = os.environ.get("ZOE_TIMEZONE", "Australia/Perth")
        params = {
            "latitude": lat, "longitude": lon, "timezone": tz,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            r.raise_for_status()
            data = r.json()
        cur = data.get("current", {})
        wmo = cur.get("weather_code", 0)
        return {
            "temp": cur.get("temperature_2m"),
            "feels_like": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"),
            "wind_speed": cur.get("wind_speed_10m"),
            "description": _WMO.get(wmo, f"WMO code {wmo}"),
            "city": city,
            "source": "open-meteo",
        }

    elif name == "weather_forecast":
        _WMO = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                45: "Fog", 48: "Icy fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
                61: "Slight rain", 63: "Rain", 65: "Heavy rain", 71: "Slight snow", 73: "Snow",
                75: "Heavy snow", 80: "Rain showers", 81: "Rain showers", 82: "Violent showers",
                85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm"}
        city = args.get("city")
        days = min(int(args.get("days", 5)), 7)
        lat = lon = None
        if not city:
            cursor = await db.execute(
                "SELECT latitude, longitude, city FROM weather_preferences WHERE user_id=?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row:
                d = dict(row)
                lat, lon, city = d.get("latitude"), d.get("longitude"), d.get("city")
        if not lat:
            default_loc = await _get_weather_default_location(db)
            lat = default_loc["latitude"]
            lon = default_loc["longitude"]
            city = city or default_loc["city"]
            tz = default_loc["timezone"]
        else:
            city = city or os.environ.get("ZOE_LOCATION_CITY", "Geraldton")
            tz = os.environ.get("ZOE_TIMEZONE", "Australia/Perth")
        params = {
            "latitude": lat, "longitude": lon, "timezone": tz,
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_sum",
            "forecast_days": days,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            r.raise_for_status()
            data = r.json()
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        codes = daily.get("weather_code", [])
        precip = daily.get("precipitation_sum", [])
        return {
            "forecast": [
                {
                    "date": dates[i] if i < len(dates) else None,
                    "temp_max": max_temps[i] if i < len(max_temps) else None,
                    "temp_min": min_temps[i] if i < len(min_temps) else None,
                    "description": _WMO.get(codes[i] if i < len(codes) else 0, "Unknown"),
                    "precipitation_mm": precip[i] if i < len(precip) else None,
                }
                for i in range(len(dates))
            ],
            "city": city,
            "source": "open-meteo",
        }

    # === SELF-AWARENESS TOOLS ===
    elif name == "zoe_get_time":
        import datetime
        tz_name = os.environ.get("ZOE_TIMEZONE", "Australia/Perth")
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(tz_name)
            now = datetime.datetime.now(tz)
        except Exception:
            now = datetime.datetime.now()
        return {
            "datetime": now.isoformat(),
            "formatted": now.strftime("%A, %d %B %Y — %I:%M %p"),
            "timezone": tz_name,
        }

    elif name == "zoe_get_status":
        import datetime
        import platform
        try:
            import psutil
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=0.1)
            mem_info = {"used_gb": round(mem.used / 1e9, 2), "total_gb": round(mem.total / 1e9, 2), "percent": mem.percent}
        except ImportError:
            mem_info = {}
            cpu = None
        agents = []
        pi_mode = os.environ.get("HERMES_FAST_PATH", "true").lower() != "true"
        jetson_mode = os.environ.get("JETSON_AGENT_MODE", "false").lower() == "true"
        if jetson_mode:
            agents.append("Jetson Agent (Gemma 4 GPU)")
        elif pi_mode:
            agents.append("Pi Agent (Gemma 4 CPU)")
        else:
            agents.append("Bonsai Agent")
        agents.append("OpenClaw (on-demand)")
        return {
            "active_agents": agents,
            "platform": platform.machine(),
            "python": platform.python_version(),
            "cpu_percent": cpu,
            "memory": mem_info,
            "mempalace_dir": os.environ.get("MEMPALACE_DATA_DIR", os.path.expanduser("~/.mempalace")),
            "datetime": datetime.datetime.now().isoformat(),
        }

    elif name == "zoe_list_skills":
        # Real OpenClaw workspace skills live under ~/.openclaw/workspace/skills.
        # Keep the legacy ~/.openclaw/skills as a fallback.
        skills_dirs = [
            os.path.expanduser("~/.openclaw/workspace/skills"),
            os.path.expanduser("~/.openclaw/skills"),
        ]
        skills: set[str] = set()
        used_dir = None
        for d in skills_dirs:
            try:
                if os.path.isdir(d):
                    used_dir = used_dir or d
                    for entry in os.scandir(d):
                        if entry.is_dir():
                            skills.add(entry.name)
            except Exception:
                continue
        return {
            "skills": sorted(skills),
            "count": len(skills),
            "skills_dir": used_dir or skills_dirs[0],
        }

    elif name == "zoe_self_capabilities":
        import datetime
        import glob
        import json as _json
        import socket

        # --- services (probe ports) ---
        def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            except Exception:
                return False

        services = [
            {"name": "zoe-data",         "port": 8000,  "up": _port_open("127.0.0.1", 8000)},
            {"name": "zoe-auth",         "port": 8001,  "up": _port_open("127.0.0.1", 8001)},
            {"name": "hermes-agent",     "port": 8642,  "up": _port_open("127.0.0.1", 8642)},
            {"name": "llama-cpp",        "port": 11435, "up": _port_open("127.0.0.1", 11435)},
            {"name": "openclaw-gateway", "port": 18789, "up": _port_open("127.0.0.1", 18789)},
            {"name": "nginx",            "port": 80,    "up": _port_open("127.0.0.1", 80)},
        ]

        # --- active agents (mirrors zoe_get_status) ---
        agents = []
        pi_mode = os.environ.get("HERMES_FAST_PATH", "true").lower() != "true"
        jetson_mode = os.environ.get("JETSON_AGENT_MODE", "false").lower() == "true"
        if jetson_mode:
            agents.append("Jetson Agent (Gemma 4 GPU)")
        elif pi_mode:
            agents.append("Pi Agent (Gemma 4 CPU)")
        else:
            agents.append("Bonsai Agent (via Hermes)")
        agents.append("OpenClaw (on-demand)")

        # --- widgets from widget-manifest.json ---
        widgets: list[str] = []
        manifest_path = "/home/zoe/assistant/services/zoe-ui/dist/js/widgets/widget-manifest.json"
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = _json.load(fh)
            for entry in manifest.get("widgets", manifest if isinstance(manifest, list) else []):
                wid = (entry.get("id") if isinstance(entry, dict) else None)
                if wid:
                    widgets.append(wid)
        except Exception:
            pass

        # --- pages from dist/*.html + touch/*.html ---
        dist_root = "/home/zoe/assistant/services/zoe-ui/dist"
        pages_desktop = sorted(
            os.path.basename(p) for p in glob.glob(os.path.join(dist_root, "*.html"))
        )
        pages_touch = sorted(
            os.path.basename(p) for p in glob.glob(os.path.join(dist_root, "touch", "*.html"))
        )

        # --- skills (prefer workspace) ---
        skills_list: list[str] = []
        for d in (
            os.path.expanduser("~/.openclaw/workspace/skills"),
            os.path.expanduser("~/.openclaw/skills"),
        ):
            try:
                if os.path.isdir(d):
                    for entry in os.scandir(d):
                        if entry.is_dir() and entry.name not in skills_list:
                            skills_list.append(entry.name)
                    break
            except Exception:
                continue

        builder_skills_present = [
            s for s in skills_list
            if s in ("zoe-widget-builder", "zoe-page-builder", "zoe-capability-extender")
        ]

        return {
            "datetime": datetime.datetime.now().isoformat(),
            "services": services,
            "active_agents": agents,
            "widgets": sorted(widgets),
            "pages": {"desktop": pages_desktop, "touch": pages_touch},
            "skills": sorted(skills_list),
            "builder_skills_installed": sorted(builder_skills_present),
            "can_build": ["widget", "page", "capability"],
            "notes": (
                "Use the builder skills to extend Zoe. "
                "Admin gate + plan-then-confirm + staged /_preview/ apply."
            ),
        }

    # === JOURNAL TOOLS ===
    elif name == "journal_create_entry":
        eid = str(uuid.uuid4())
        tags_str = args.get("tags")
        tags_json = json.dumps([t.strip() for t in tags_str.split(",")]) if tags_str else None
        await db.execute(
            """INSERT INTO journal_entries (
                id, user_id, content, title, mood, mood_score, tags, visibility, deleted
            ) VALUES (?,?,?,?,?,?,?,'personal',0)""",
            (eid, user_id, args["content"], args.get("title"), args.get("mood"),
             args.get("mood_score"), tags_json),
        )
        await db.commit()
        result = {"id": eid, "title": args.get("title"), "mood": args.get("mood")}
        await _notify_ui("journal", "entry_created", result)
        try:
            from routers.journal import _store_journal_memory  # type: ignore
            await _store_journal_memory(
                db, user_id,
                {**result, "content": args["content"], "mood_score": args.get("mood_score")},
                "created",
            )
        except Exception as exc:
            _mcp_log.info("mcp journal_create_entry memory mirror skipped: %s", exc)
        return {**result, "status": "created"}

    elif name == "journal_list_entries":
        limit = args.get("limit", 10)
        conditions = ["user_id=? AND deleted=0"]
        params = [user_id]
        if args.get("start_date"):
            conditions.append("date(created_at) >= ?")
            params.append(args["start_date"])
        if args.get("end_date"):
            conditions.append("date(created_at) <= ?")
            params.append(args["end_date"])
        if args.get("mood"):
            conditions.append("mood = ?")
            params.append(args["mood"])
        if args.get("search"):
            conditions.append("(title LIKE ? OR content LIKE ?)")
            p = f"%{args['search']}%"
            params.extend([p, p])
        where = " AND ".join(conditions)
        params.append(limit)
        cursor = await db.execute(
            f"SELECT id, title, mood, mood_score, created_at FROM journal_entries WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        return {"entries": [dict(r) for r in rows]}

    elif name == "journal_get_streak":
        cursor = await db.execute(
            "SELECT COUNT(*) FROM journal_entries WHERE user_id=? AND deleted=0",
            (user_id,),
        )
        row = await cursor.fetchone()
        total = row[0] if row else 0
        cursor = await db.execute(
            "SELECT DISTINCT date(created_at) as d FROM journal_entries WHERE user_id=? AND deleted=0 ORDER BY d DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        dates_sorted = sorted([r[0] for r in rows if r[0]], reverse=True)
        current_streak = 0
        longest_streak = 0
        if dates_sorted:
            for i, d in enumerate(dates_sorted):
                expected = (date.today() - timedelta(days=i)).isoformat()
                if d == expected:
                    current_streak += 1
                else:
                    break
            run = 1
            for i in range(1, len(dates_sorted)):
                curr_d = datetime.strptime(dates_sorted[i], "%Y-%m-%d").date()
                prev_d = datetime.strptime(dates_sorted[i - 1], "%Y-%m-%d").date()
                if (prev_d - curr_d).days == 1:
                    run += 1
                else:
                    longest_streak = max(longest_streak, run)
                    run = 1
            longest_streak = max(longest_streak, run)
        return {"current_streak": current_streak, "longest_streak": longest_streak, "total_entries": total}

    elif name == "journal_get_prompts":
        prompts = [
            "What was the highlight of your day?",
            "What are you grateful for today?",
            "What challenged you today, and how did you handle it?",
            "Describe one thing you learned today.",
            "How are you feeling right now, and why?",
            "What would you do differently if you could redo today?",
            "What made you smile today?",
            "What are you looking forward to tomorrow?",
            "Describe a moment of connection you had today.",
            "What did you do today that you're proud of?",
        ]
        return {"prompts": random.sample(prompts, min(5, len(prompts)))}

    elif name == "journal_on_this_day":
        today_md = date.today().strftime("%m-%d")
        cursor = await db.execute(
            """SELECT id, title, mood, created_at FROM journal_entries
             WHERE user_id=? AND deleted=0 AND strftime('%m-%d', created_at)=?
             AND date(created_at) < date('now') ORDER BY created_at DESC""",
            (user_id, today_md),
        )
        rows = await cursor.fetchall()
        return {"entries": [dict(r) for r in rows]}

    # === TRANSACTION TOOLS ===
    elif name == "transaction_create":
        tid = str(uuid.uuid4())
        tx_date = args.get("transaction_date", date.today().isoformat())
        tx_type = args.get("type", "expense")
        await db.execute(
            """INSERT INTO transactions (
                id, user_id, description, amount, type, transaction_date,
                category, payment_method, status, visibility, deleted
            ) VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
            (tid, user_id, args["description"], args["amount"], tx_type, tx_date,
             args.get("category", "general"), args.get("payment_method"),
             "completed", "family"),
        )
        await db.commit()
        result = {"id": tid, "description": args["description"], "amount": args["amount"],
                  "type": tx_type, "transaction_date": tx_date}
        await _notify_ui("transactions", "transaction_created", result)
        return {**result, "date": tx_date, "status": "created"}

    elif name == "transaction_list":
        limit = args.get("limit", 20)
        conditions = ["(visibility='family' OR user_id=?) AND deleted=0"]
        params = [user_id]
        if args.get("start_date"):
            conditions.append("transaction_date >= ?")
            params.append(args["start_date"])
        if args.get("end_date"):
            conditions.append("transaction_date <= ?")
            params.append(args["end_date"])
        if args.get("type"):
            conditions.append("type = ?")
            params.append(args["type"])
        if args.get("category"):
            conditions.append("category = ?")
            params.append(args["category"])
        where = " AND ".join(conditions)
        params.append(limit)
        cursor = await db.execute(
            f"SELECT id, description, amount, type, transaction_date, category FROM transactions WHERE {where} ORDER BY transaction_date DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        return {"transactions": [dict(r) for r in rows]}

    elif name == "transaction_summary":
        period = args.get("period", "week")
        today_d = date.today()
        if period == "month":
            start = today_d.replace(day=1)
        else:
            start = today_d - timedelta(days=6)
        start_str = start.isoformat()
        end_str = today_d.isoformat()
        cursor = await db.execute(
            """SELECT type, SUM(amount) as total FROM transactions
             WHERE (visibility='family' OR user_id=?) AND deleted=0
             AND transaction_date >= ? AND transaction_date <= ?
             GROUP BY type""",
            (user_id, start_str, end_str),
        )
        rows = await cursor.fetchall()
        total_expense = 0
        total_income = 0
        for r in rows:
            d = dict(r)
            if d["type"] == "expense":
                total_expense += d["total"]
            else:
                total_income += d["total"]
        return {
            "period": period, "start_date": start_str, "end_date": end_str,
            "total_expense": total_expense, "total_income": total_income,
            "net": total_income - total_expense,
        }

    # === CALENDAR CRUD ===
    elif name == "calendar_update_event":
        eid = args["event_id"]
        cursor = await db.execute("SELECT id FROM events WHERE id=? AND deleted=0", (eid,))
        if not await cursor.fetchone():
            return {"error": f"Event {eid} not found"}
        updates, params = [], []
        for field in ("title", "start_date", "start_time", "end_time", "location", "category"):
            if field in args:
                updates.append(f"{field}=?")
                params.append(args[field])
        if "all_day" in args:
            updates.append("all_day=?")
            params.append(1 if args["all_day"] else 0)
        if not updates:
            return {"error": "No fields to update"}
        updates.append("updated_at=datetime('now')")
        params.append(eid)
        await db.execute(f"UPDATE events SET {','.join(updates)} WHERE id=?", params)
        await db.commit()
        await _notify_ui("calendar", "event_updated", {"id": eid})
        return {"id": eid, "status": "updated"}

    elif name == "calendar_delete_event":
        eid = args["event_id"]
        cursor = await db.execute("SELECT id FROM events WHERE id=? AND deleted=0", (eid,))
        if not await cursor.fetchone():
            return {"error": f"Event {eid} not found"}
        await db.execute("UPDATE events SET deleted=1, updated_at=datetime('now') WHERE id=?", (eid,))
        await db.commit()
        await _notify_ui("calendar", "event_deleted", {"id": eid})
        return {"id": eid, "status": "deleted"}

    # === REMINDER CRUD ===
    elif name == "reminder_update":
        rid = args["reminder_id"]
        cursor = await db.execute("SELECT id FROM reminders WHERE id=? AND deleted=0", (rid,))
        if not await cursor.fetchone():
            return {"error": f"Reminder {rid} not found"}
        updates, params = [], []
        for field in ("title", "due_date", "due_time", "priority"):
            if field in args:
                updates.append(f"{field}=?")
                params.append(args[field])
        if not updates:
            return {"error": "No fields to update"}
        updates.append("updated_at=datetime('now')")
        params.append(rid)
        await db.execute(f"UPDATE reminders SET {','.join(updates)} WHERE id=?", params)
        await db.commit()
        await _notify_ui("reminders", "reminder_updated", {"id": rid})
        return {"id": rid, "status": "updated"}

    elif name == "reminder_delete":
        rid = args["reminder_id"]
        cursor = await db.execute("SELECT id FROM reminders WHERE id=? AND deleted=0", (rid,))
        if not await cursor.fetchone():
            return {"error": f"Reminder {rid} not found"}
        await db.execute("UPDATE reminders SET deleted=1, is_active=0, updated_at=datetime('now') WHERE id=?", (rid,))
        await db.commit()
        await _notify_ui("reminders", "reminder_deleted", {"id": rid})
        return {"id": rid, "status": "deleted"}

    elif name == "reminder_snooze":
        rid = args["reminder_id"]
        minutes = args.get("minutes", 30)
        cursor = await db.execute("SELECT id, due_date, due_time FROM reminders WHERE id=? AND deleted=0", (rid,))
        row = await cursor.fetchone()
        if not row:
            return {"error": f"Reminder {rid} not found"}
        now = datetime.now()
        new_time = now + timedelta(minutes=minutes)
        new_date = new_time.strftime("%Y-%m-%d")
        new_time_str = new_time.strftime("%H:%M")
        await db.execute(
            "UPDATE reminders SET due_date=?, due_time=?, acknowledged=0, updated_at=datetime('now') WHERE id=?",
            (new_date, new_time_str, rid),
        )
        await db.commit()
        result = {"id": rid, "snoozed_until": f"{new_date} {new_time_str}"}
        await _notify_ui("reminders", "reminder_snoozed", result)
        return {**result, "status": "snoozed"}

    # === NOTE CRUD ===
    elif name == "note_update":
        nid = args["note_id"]
        cursor = await db.execute("SELECT id FROM notes WHERE id=? AND deleted=0", (nid,))
        if not await cursor.fetchone():
            return {"error": f"Note {nid} not found"}
        updates, params = [], []
        for field in ("title", "content", "category"):
            if field in args:
                updates.append(f"{field}=?")
                params.append(args[field])
        if not updates:
            return {"error": "No fields to update"}
        updates.append("updated_at=datetime('now')")
        params.append(nid)
        await db.execute(f"UPDATE notes SET {','.join(updates)} WHERE id=?", params)
        await db.commit()
        await _notify_ui("notes", "note_updated", {"id": nid})
        return {"id": nid, "status": "updated"}

    elif name == "note_delete":
        nid = args["note_id"]
        cursor = await db.execute("SELECT id FROM notes WHERE id=? AND deleted=0", (nid,))
        if not await cursor.fetchone():
            return {"error": f"Note {nid} not found"}
        await db.execute("UPDATE notes SET deleted=1, updated_at=datetime('now') WHERE id=?", (nid,))
        await db.commit()
        await _notify_ui("notes", "note_deleted", {"id": nid})
        return {"id": nid, "status": "deleted"}

    # === PEOPLE CRUD ===
    elif name == "people_update":
        pid = args["person_id"]
        cursor = await db.execute("SELECT id FROM people WHERE id=? AND deleted=0", (pid,))
        if not await cursor.fetchone():
            return {"error": f"Person {pid} not found"}
        updates, params = [], []
        for field in ("name", "relationship", "birthday", "phone", "email", "notes"):
            if field in args:
                updates.append(f"{field}=?")
                params.append(args[field])
        if not updates:
            return {"error": "No fields to update"}
        updates.append("updated_at=datetime('now')")
        params.append(pid)
        await db.execute(f"UPDATE people SET {','.join(updates)} WHERE id=?", params)
        await db.commit()
        await _notify_ui("all", "people:updated", {"id": pid})
        return {"id": pid, "status": "updated"}

    elif name == "people_delete":
        pid = args["person_id"]
        cursor = await db.execute("SELECT id FROM people WHERE id=? AND deleted=0", (pid,))
        if not await cursor.fetchone():
            return {"error": f"Person {pid} not found"}
        await db.execute("UPDATE people SET deleted=1, updated_at=datetime('now') WHERE id=?", (pid,))
        await db.commit()
        await _notify_ui("all", "people:deleted", {"id": pid})
        return {"id": pid, "status": "deleted"}

    # === NOTIFICATION TOOL ===
    elif name == "notification_create":
        nid = str(uuid.uuid4())
        await db.execute(
            """INSERT INTO notifications (
                id, user_id, title, message, type, delivered, created_at
            ) VALUES (?,?,?,?,?,0,datetime('now'))""",
            (nid, user_id, args["title"], args["message"],
             args.get("type", "info")),
        )
        await db.commit()
        result = {"id": nid, "title": args["title"], "message": args["message"],
                  "type": args.get("type", "info")}
        await _notify_ui("all", "notification_created", result)
        return {**result, "status": "created"}

    elif name == "panel_navigate":
        url = str(args.get("url") or "").strip()
        if not url or not url.startswith(("http://", "https://")):
            return {"error": "url must be an http/https URL"}
        panel_id = args.get("panel_id") or None
        label = args.get("label") or f"Opening {url[:60]}"
        msg = await _enqueue_panel_tool(
            db,
            user_id_fallback=user_id,
            panel_id=panel_id,
            action_type="panel_navigate",
            payload={"url": url, "label": label},
        )
        return {"ok": True, "action": "panel_navigate", "url": url, "panel_id": panel_id, "queued": msg}

    elif name == "panel_clear":
        panel_id = args.get("panel_id") or None
        msg = await _enqueue_panel_tool(
            db,
            user_id_fallback=user_id,
            panel_id=panel_id,
            action_type="panel_clear",
            payload={},
        )
        return {"ok": True, "action": "panel_clear", "panel_id": panel_id, "queued": msg}

    elif name == "panel_show_fullscreen":
        image_b64 = str(args.get("image_base64") or "").strip()
        if not image_b64:
            return {"error": "image_base64 is required"}
        panel_id = args.get("panel_id") or None
        caption = args.get("caption") or ""
        msg = await _enqueue_panel_tool(
            db,
            user_id_fallback=user_id,
            panel_id=panel_id,
            action_type="panel_show_fullscreen",
            payload={"image_base64": image_b64, "caption": caption},
        )
        return {"ok": True, "action": "panel_show_fullscreen", "panel_id": panel_id, "queued": msg}

    elif name == "panel_browser_screenshot":
        # Screenshot-to-panel via broker-backed executor with normalized evidence.
        panel_id = args.get("panel_id") or None
        caption = args.get("caption") or ""
        navigate_to = args.get("navigate_to") or None
        plan = _BROWSER_BROKER.plan_action(
            action="capture_screenshot",
            params={
                "navigate_to": navigate_to or "",
                "timeout_s": 15.0,
                "screenshot_timeout_s": 20.0,
            },
            user_id=user_id,
            session_id=f"mcp:{name}",
            action_class="read_only_research",
            requested_surface="openclawLocal",
        )
        broker_result = await _BROWSER_BROKER.execute(plan)
        if not broker_result.get("ok"):
            # Graceful fallback: still navigate panel if URL exists.
            if navigate_to:
                fallback_msg = await _enqueue_panel_tool(
                    db,
                    user_id_fallback=user_id,
                    panel_id=panel_id,
                    action_type="panel_navigate",
                    payload={
                        "url": navigate_to,
                        "label": caption or "Browser screenshot unavailable — showing live page instead",
                    },
                )
                return {
                    "ok": False,
                    "degraded_mode": True,
                    "error": broker_result.get("error", "Screenshot failed"),
                    "backend": broker_result.get("surface", "openclawLocal"),
                    "plan_id": broker_result.get("plan_id"),
                    "fallback_action": "panel_navigate",
                    "queued": fallback_msg,
                }
            return {
                "error": broker_result.get("error", "Screenshot failed"),
                "backend": broker_result.get("surface", "openclawLocal"),
                "plan_id": broker_result.get("plan_id"),
            }
        image_b64 = str(broker_result.get("image_base64") or "").strip()
        if not image_b64:
            return {"error": "No screenshot data in broker response", "backend": "openclawLocal"}

        msg = await _enqueue_panel_tool(
            db,
            user_id_fallback=user_id,
            panel_id=panel_id,
            action_type="panel_show_fullscreen",
            payload={"image_base64": image_b64, "caption": caption or (f"Browser view: {navigate_to}" if navigate_to else "Browser view")},
        )
        return {
            "ok": True,
            "action": "panel_browser_screenshot",
            "panel_id": panel_id,
            "queued": msg,
            "backend": broker_result.get("surface", "openclawLocal"),
            "plan_id": broker_result.get("plan_id"),
            "evidence": broker_result.get("evidence", {}),
        }

    elif name == "browser_get_capabilities":
        return {
            "ok": True,
            "default_backend": _BROWSER_BROKER.default_surface(),
            "capabilities": _BROWSER_BROKER.capabilities(),
        }

    elif name == "browser_compare_backends":
        report = _BROWSER_BROKER.compare_backends()
        return {"ok": True, **report}

    elif name == "panel_announce":
        message = str(args.get("message") or "").strip()
        if not message:
            return {"error": "message is required"}
        panel_id = args.get("panel_id") or "all"
        msg = await _enqueue_panel_tool(
            db,
            user_id_fallback=user_id,
            panel_id=panel_id,
            action_type="panel_announce",
            payload={"message": message},
        )
        return {"ok": True, "action": "panel_announce", "panel_id": panel_id, "message": message, "queued": msg}

    elif name == "panel_request_auth":
        panel_id = str(args.get("panel_id") or "").strip()
        action_context = str(args.get("action_context") or "Authorise action").strip()
        if not panel_id:
            return {"error": "panel_id is required"}
        import uuid as _uuid
        import time as _time
        from datetime import datetime as _dt, timezone as _tz
        challenge_id = _uuid.uuid4().hex
        ttl = 120
        expires_at = _dt.fromtimestamp(_time.time() + ttl, tz=_tz.utc).isoformat()
        await db.execute(
            """INSERT INTO panel_auth_challenges (challenge_id, panel_id, user_id, action_context, status, expires_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (challenge_id, panel_id, user_id, action_context, expires_at),
        )
        await db.commit()
        await _notify_ui("all", "panel_pin_request", {
            "panel_id": panel_id,
            "challenge_id": challenge_id,
            "action_context": action_context,
            "expires_at": expires_at,
        })
        return {"ok": True, "challenge_id": challenge_id, "panel_id": panel_id, "expires_at": expires_at,
                "note": "Show PIN pad to user; call panel_check_auth to confirm approval."}

    elif name == "panel_check_auth":
        challenge_id = str(args.get("challenge_id") or "").strip()
        if not challenge_id:
            return {"error": "challenge_id is required"}
        row = await (await db.execute(
            "SELECT status, expires_at FROM panel_auth_challenges WHERE challenge_id = ?", (challenge_id,)
        )).fetchone()
        if not row:
            return {"error": "Challenge not found"}
        return {"challenge_id": challenge_id, "status": row["status"], "expires_at": row["expires_at"]}

    elif name == "panel_set_mode":
        mode = str(args.get("mode") or "ambient").strip()
        panel_id = args.get("panel_id") or None
        msg = await _enqueue_panel_tool(
            db,
            user_id_fallback=user_id,
            panel_id=panel_id,
            action_type="panel_set_mode",
            payload={"mode": mode},
        )
        return {"ok": True, "action": "panel_set_mode", "mode": mode, "panel_id": panel_id, "queued": msg}

    elif name == "panel_show_smart_home":
        panel_id = args.get("panel_id") or None
        entities = args.get("entities") or []
        title = args.get("title") or "Smart Home"
        dismiss_after = int(args.get("dismiss_after") or 30)
        # If no entities supplied, fetch from HA bridge
        if not entities:
            _ha_bridge = os.environ.get("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007")
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    r = await client.get(f"{_ha_bridge}/entities")
                    if r.status_code == 200:
                        all_ents = r.json()
                        if isinstance(all_ents, list):
                            all_ents = all_ents
                        elif isinstance(all_ents, dict):
                            all_ents = all_ents.get("entities", [])
                        # Filter to actionable domains
                        entities = [
                            e for e in all_ents
                            if str(e.get("entity_id", "")).startswith(("light.", "switch.", "input_boolean."))
                        ][:12]  # Cap at 12 for UI
            except Exception:
                pass
        msg = await _enqueue_panel_tool(
            db,
            user_id_fallback=user_id,
            panel_id=panel_id,
            action_type="panel_show_smart_home",
            payload={"entities": entities, "title": title, "dismiss_after": dismiss_after},
        )
        return {"ok": True, "action": "panel_show_smart_home", "entity_count": len(entities), "panel_id": panel_id, "queued": msg}

    elif name == "panel_show_media":
        panel_id = args.get("panel_id") or None
        payload = {k: v for k, v in args.items() if k != "panel_id"}
        msg = await _enqueue_panel_tool(
            db,
            user_id_fallback=user_id,
            panel_id=panel_id,
            action_type="panel_show_media",
            payload=payload,
        )
        return {"ok": True, "action": "panel_show_media", "panel_id": panel_id, "queued": msg}

    elif name == "media_get_now_playing":
        entity_id = str(args.get("entity_id") or "").strip()
        _ha_bridge = os.environ.get("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                if entity_id:
                    r = await client.get(f"{_ha_bridge}/state/{entity_id}")
                    r.raise_for_status()
                    state = r.json()
                    attrs = state.get("attributes") or {}
                    return {
                        "entity_id": entity_id,
                        "state": state.get("state"),
                        "title": attrs.get("media_title"),
                        "artist": attrs.get("media_artist"),
                        "album": attrs.get("media_album_name"),
                        "album_art": attrs.get("entity_picture"),
                        "volume": attrs.get("volume_level"),
                    }
                else:
                    r = await client.get(f"{_ha_bridge}/entities")
                    r.raise_for_status()
                    ents = r.json()
                    if isinstance(ents, dict):
                        ents = ents.get("entities", [])
                    players = [e for e in ents if str(e.get("entity_id", "")).startswith("media_player.")]
                    results = []
                    for p in players:
                        attrs = p.get("attributes") or {}
                        results.append({
                            "entity_id": p.get("entity_id"),
                            "state": p.get("state"),
                            "title": attrs.get("media_title"),
                            "artist": attrs.get("media_artist"),
                            "album_art": attrs.get("entity_picture"),
                        })
                    return {"players": results}
        except Exception as exc:
            return {"error": f"HA bridge error: {exc}"}

    elif name == "ambient_search":
        query = str(args.get("query", "")).strip()
        if not query:
            return {"error": "query is required"}
        room = args.get("room")
        speaker_id = args.get("speaker_id")
        date_from = args.get("date_from")
        date_to = args.get("date_to")
        limit = min(int(args.get("limit", 10)), 50)

        # Build WHERE clause for base table filters.
        conditions = []
        params: list = [query]  # FTS match param first
        if room:
            conditions.append("m.room = ?")
            params.append(room)
        if speaker_id:
            conditions.append("m.speaker_id = ?")
            params.append(speaker_id)
        if date_from:
            conditions.append("m.timestamp >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("m.timestamp <= ?")
            params.append(date_to)
        params.append(limit)

        where = ("AND " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT m.id, m.timestamp, m.panel_id, m.room, m.speaker_id, m.transcript
            FROM ambient_memory_fts f
            JOIN ambient_memory m ON m.id = f.rowid
            WHERE ambient_memory_fts MATCH ?
            {where}
            ORDER BY rank
            LIMIT ?
        """
        try:
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
            results = [
                {
                    "id": r[0],
                    "timestamp": r[1],
                    "panel_id": r[2],
                    "room": r[3],
                    "speaker_id": r[4],
                    "transcript": r[5],
                }
                for r in rows
            ]
            return {"results": results, "count": len(results), "query": query}
        except Exception as exc:
            return {"error": f"ambient_search failed: {exc}"}

    # === MEMORY TOOLS ================================================
    elif name in {"memory_add", "memory_search", "memory_list", "memory_review", "memory_forget"}:
        try:
            from memory_service import MemoryServiceError, get_memory_service
        except Exception as exc:
            return {"error": f"memory service unavailable: {exc}"}
        svc = get_memory_service()

        if name == "memory_add":
            content = (args.get("content") or "").strip()
            if not content:
                return {"error": "content required"}
            try:
                ref = await svc.ingest(
                    content,
                    user_id=user_id,
                    source="mcp",
                    memory_type=args.get("memory_type", "fact"),
                    confidence=float(args.get("confidence", 0.85)),
                    status=args.get("status", "approved"),
                    tags=list(args.get("tags") or []),
                    entity_type=args.get("entity_type"),
                    entity_id=args.get("entity_id"),
                    expires_at=args.get("expires_at"),
                )
            except MemoryServiceError as exc:
                return {"error": str(exc)}
            if ref is None:
                return {"status": "skipped", "reason": "duplicate_or_filtered"}
            return {
                "id": ref.id,
                "status": ref.metadata.get("status", "approved"),
                "created": True,
            }

        if name == "memory_search":
            query = (args.get("query") or "").strip()
            if not query:
                return {"error": "query required"}
            limit = max(1, min(int(args.get("limit") or 8), 50))
            try:
                refs = await svc.search(query, user_id=user_id, limit=limit)
            except MemoryServiceError as exc:
                return {"error": str(exc)}
            mtype_filter = (args.get("memory_type") or "").strip().lower()
            results = []
            for r in refs:
                md = r.metadata or {}
                if mtype_filter and (md.get("memory_type") or "").lower() != mtype_filter:
                    continue
                results.append({
                    "id": r.id,
                    "content": r.text,
                    "memory_type": md.get("memory_type"),
                    "confidence": md.get("confidence"),
                    "status": md.get("status"),
                    "score": getattr(r, "score", None),
                })
            return {"results": results, "count": len(results)}

        if name == "memory_list":
            status = (args.get("status") or "pending").lower()
            limit = max(1, min(int(args.get("limit") or 25), 200))
            try:
                refs = await svc.list_by_status(user_id=user_id, status=status, limit=limit)
            except MemoryServiceError as exc:
                return {"error": str(exc)}
            return {
                "status": status,
                "count": len(refs),
                "items": [
                    {
                        "id": r.id,
                        "content": r.text,
                        "memory_type": (r.metadata or {}).get("memory_type"),
                        "confidence": (r.metadata or {}).get("confidence"),
                        "status": (r.metadata or {}).get("status"),
                        "created_at": (r.metadata or {}).get("created_at"),
                    }
                    for r in refs
                ],
            }

        if name == "memory_review":
            mem_id = args.get("memory_id") or ""
            decision = (args.get("decision") or "").lower()
            if not mem_id or decision not in {"approve", "reject", "edit"}:
                return {"error": "memory_id and decision=(approve|reject|edit) required"}
            # Ownership check: svc.get resolves the row; MemoryService.review
            # also audits, but we want a friendly error rather than a raise.
            try:
                existing = await svc.get(mem_id)
            except MemoryServiceError as exc:
                return {"error": str(exc)}
            if existing is None:
                return {"error": "memory not found"}
            if (existing.metadata or {}).get("user_id") != user_id:
                # No role info on the MCP path; strict mode → deny.
                return {"error": "forbidden: memory belongs to another user"}
            try:
                ref = await svc.review(
                    mem_id,
                    decision=decision,
                    actor=user_id,
                    edits=args.get("edits"),
                    note=args.get("note"),
                )
            except MemoryServiceError as exc:
                return {"error": str(exc)}
            return {
                "id": ref.id,
                "status": (ref.metadata or {}).get("status"),
                "decision": decision,
            }

        if name == "memory_forget":
            mem_id = args.get("memory_id") or ""
            if not mem_id:
                return {"error": "memory_id required"}
            try:
                existing = await svc.get(mem_id)
            except MemoryServiceError as exc:
                return {"error": str(exc)}
            if existing is None:
                return {"status": "not_found"}
            if (existing.metadata or {}).get("user_id") != user_id:
                return {"error": "forbidden: memory belongs to another user"}
            try:
                await svc.review(
                    mem_id,
                    decision="reject",
                    actor=user_id,
                    note=args.get("note") or "memory_forget",
                )
            except MemoryServiceError as exc:
                return {"error": str(exc)}
            return {"id": mem_id, "status": "rejected"}

    else:
        return {"error": f"Unknown tool: {name}"}


async def run_stdio_server():
    """Run as MCP stdio server for OpenClaw integration."""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            msg = json.loads(line.decode())
        except json.JSONDecodeError:
            continue

        if msg.get("method") == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {"tools": TOOLS},
            }
        elif msg.get("method") == "tools/call":
            tool_name = msg["params"]["name"]
            tool_args = msg["params"].get("arguments", {})
            result_text = await handle_tool(tool_name, tool_args)
            response = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                    "isError": False,
                },
            }
        elif msg.get("method") == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "zoe-data", "version": "1.0.0"},
                },
            }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {},
            }

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(run_stdio_server())
