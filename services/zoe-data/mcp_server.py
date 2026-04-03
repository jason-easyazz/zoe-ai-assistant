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


async def _execute_tool(db, name: str, args: dict):
    user_id = args.pop("_user_id", args.pop("user_id", "family-admin"))

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
                "SELECT l.id, l.name, li.id as item_id, li.text, li.completed, li.quantity, li.category FROM lists l LEFT JOIN list_items li ON l.id = li.list_id AND li.deleted=0 WHERE l.list_type=? AND l.name LIKE ? AND l.deleted=0 ORDER BY li.sort_order",
                (lt, f"%{ln}%"),
            )
        else:
            cursor = await db.execute(
                "SELECT l.id, l.name, li.id as item_id, li.text, li.completed, li.quantity, li.category FROM lists l LEFT JOIN list_items li ON l.id = li.list_id AND li.deleted=0 WHERE l.list_type=? AND l.deleted=0 ORDER BY l.name, li.sort_order",
                (lt,),
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
            "SELECT id FROM lists WHERE list_type=? AND name=? AND deleted=0 LIMIT 1",
            (lt, ln),
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
            "SELECT li.id, li.list_id FROM list_items li JOIN lists l ON li.list_id = l.id WHERE l.list_type=? AND li.text LIKE ? AND li.deleted=0 AND l.deleted=0 LIMIT 1",
            (lt, f"%{text}%"),
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

    # === WEATHER TOOLS ===
    elif name == "weather_current":
        city = args.get("city")
        lat, lon = None, None
        if not city:
            cursor = await db.execute(
                "SELECT latitude, longitude, city FROM weather_preferences WHERE user_id=?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row:
                d = dict(row)
                lat, lon, city = d.get("latitude"), d.get("longitude"), d.get("city")
        if not OPENWEATHERMAP_API_KEY:
            return {"error": "No weather API key configured"}
        params = {"appid": OPENWEATHERMAP_API_KEY, "units": "metric"}
        if lat and lon:
            params["lat"] = lat
            params["lon"] = lon
        elif city:
            params["q"] = city
        else:
            return {"error": "No location configured. Set weather preferences first."}
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.openweathermap.org/data/2.5/weather", params=params)
            r.raise_for_status()
            data = r.json()
        return {
            "temp": data.get("main", {}).get("temp"),
            "feels_like": data.get("main", {}).get("feels_like"),
            "humidity": data.get("main", {}).get("humidity"),
            "description": data.get("weather", [{}])[0].get("description") if data.get("weather") else None,
            "city": data.get("name"),
            "country": data.get("sys", {}).get("country"),
        }

    elif name == "weather_forecast":
        city = args.get("city")
        days = args.get("days", 5)
        lat, lon = None, None
        if not city:
            cursor = await db.execute(
                "SELECT latitude, longitude, city FROM weather_preferences WHERE user_id=?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row:
                d = dict(row)
                lat, lon, city = d.get("latitude"), d.get("longitude"), d.get("city")
        if not OPENWEATHERMAP_API_KEY:
            return {"error": "No weather API key configured"}
        params = {"appid": OPENWEATHERMAP_API_KEY, "units": "metric"}
        if lat and lon:
            params["lat"] = lat
            params["lon"] = lon
        elif city:
            params["q"] = city
        else:
            return {"error": "No location configured. Set weather preferences first."}
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.openweathermap.org/data/2.5/forecast", params=params)
            r.raise_for_status()
            data = r.json()
        items = data.get("list", [])[:days]
        return {
            "forecast": [
                {
                    "datetime": item.get("dt_txt"),
                    "temp": item.get("main", {}).get("temp"),
                    "description": item.get("weather", [{}])[0].get("description") if item.get("weather") else None,
                }
                for item in items
            ],
            "city": data.get("city", {}).get("name"),
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
