"""
Async LLM slot extraction for intent router create intents.

Uses forced function calling (tool_choice=required, single tool per intent,
max_tokens=120) so the model always returns structured args, never free text.

Each extractor maps the LLM's tool arguments to the slot keys that
_build_command in intent_router.py expects.  Returns None on any failure
so callers fall through to the Zoe Agent.
"""
import datetime
import json
import logging
from typing import Optional

import httpx

from gemma_endpoint import gemma_base

logger = logging.getLogger(__name__)

_MODEL_NAME: str = "gemma-4-E4B-it-qat-UD-Q4_K_XL"


def _today_prefix() -> str:
    today = datetime.date.today()
    return (
        f"Today is {today.strftime('%A, %B %-d, %Y')}. "
        "If the date is not stated, default to today."
    )


def _normalize_date(raw: str) -> str:
    """Guarantee YYYY-MM-DD; model sometimes omits leading zeros (2026-5-8)."""
    parts = raw.split("-")
    if len(parts) == 3:
        return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
    return raw


async def _call_with_tool(text: str, tool_schema: dict) -> Optional[dict]:
    """POST to local LLM with a single forced tool. Returns parsed args or None."""
    payload = {
        "model": _MODEL_NAME,
        "messages": [
            {"role": "system", "content": _today_prefix()},
            {"role": "user", "content": text},
        ],
        "tools": [tool_schema],
        "tool_choice": "required",
        "max_tokens": 120,
        "temperature": 0,
        "thinking_budget": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.post(f"{gemma_base()}/v1/chat/completions", json=payload)
            r.raise_for_status()
        data = r.json()
        tc = (data["choices"][0]["message"].get("tool_calls") or [])
        if not tc:
            logger.warning("nlu_extractor: no tool_call for intent (text=%s)", text[:60])
            return None
        return json.loads(tc[0]["function"]["arguments"])
    except Exception as exc:
        logger.warning("nlu_extractor: LLM call failed: %s", exc)
        return None


# ─── Tool schemas ─────────────────────────────────────────────────────────────

_CALENDAR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "calendar_create_event",
        "description": "Create a calendar event.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "start_time": {
                    "type": "string",
                    "description": "HH:MM 24-hour format, or empty string if no time given",
                },
                "category": {
                    "type": "string",
                    "description": "health, work, family, or general",
                },
            },
            "required": ["title", "start_date"],
        },
    },
}

_REMINDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reminder_create",
        "description": "Create a reminder.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "What to be reminded about"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "due_time": {
                    "type": "string",
                    "description": "HH:MM 24-hour format, or empty string if no time given",
                },
            },
            "required": ["title", "due_date"],
        },
    },
}

_LIST_ADD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_add_item",
        "description": "Add an item to a named list.",
        "parameters": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "The item to add"},
                "list_type": {
                    "type": "string",
                    "enum": ["shopping", "personal", "work", "tasks", "bucket"],
                    "description": "Which list. Use shopping for groceries, personal for tasks/todos.",
                },
            },
            "required": ["item", "list_type"],
        },
    },
}

_NOTE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "note_create",
        "description": "Create a note.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title (max 60 chars)"},
                "content": {"type": "string", "description": "Full note content"},
            },
            "required": ["title", "content"],
        },
    },
}

_PEOPLE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "people_create",
        "description": "Add a new contact or person.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "relationship": {
                    "type": "string",
                    "enum": ["friend", "colleague", "family", "neighbor"],
                },
            },
            "required": ["name", "relationship"],
        },
    },
}

_PEOPLE_FACT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "people_fact",
        "description": "Extract a fact the user is teaching about a person they know.",
        "parameters": {
            "type": "object",
            "properties": {
                "person": {"type": "string", "description": "Person's name if given, else the relationship word e.g. 'mum','dad'"},
                "relationship": {"type": "string", "enum": ["mother", "father", "sister", "brother", "son", "daughter", "wife", "husband", "partner", "friend", "colleague", "family", "other"]},
                "attribute": {"type": "string", "description": "what the fact is about, e.g. 'name','birthday','likes','hobby','job'"},
                "value": {"type": "string", "description": "the fact value, e.g. 'Janice','NCIS','teacher'"},
                "date": {"type": "string", "description": "YYYY-MM-DD if a date is stated, else empty"},
            },
            "required": ["attribute", "value"],
        },
    },
}

_TRANSACTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "transaction_create",
        "description": "Log a purchase or expense.",
        "parameters": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "amount": {"type": "number", "description": "Amount in dollars"},
            },
            "required": ["description", "amount"],
        },
    },
}

_JOURNAL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "journal_create_entry",
        "description": "Create a journal or diary entry.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Journal entry text"},
            },
            "required": ["content"],
        },
    },
}


# ─── Per-intent extractors ────────────────────────────────────────────────────

async def _extract_calendar(text: str) -> Optional[dict]:
    args = await _call_with_tool(text, _CALENDAR_SCHEMA)
    if not args:
        return None
    title = (args.get("title") or "Event").strip() or "Event"
    raw_date = (args.get("start_date") or "").strip()
    date = _normalize_date(raw_date) if raw_date else datetime.date.today().isoformat()
    time_ = (args.get("start_time") or "").strip()
    # Honour any category the model inferred; fall back to keyword heuristic.
    category = (args.get("category") or "").strip()
    if not category or category not in {"health", "work", "family", "general"}:
        from intent_router import _infer_event_category  # lazy — avoids circular at load time
        category = _infer_event_category(title)
    return {"title": title, "date": date, "time": time_, "category": category}


async def _extract_reminder(text: str) -> Optional[dict]:
    args = await _call_with_tool(text, _REMINDER_SCHEMA)
    if not args:
        return None
    title = (args.get("title") or "").strip()
    raw_date = (args.get("due_date") or "").strip()
    date = _normalize_date(raw_date) if raw_date else datetime.date.today().isoformat()
    time_ = (args.get("due_time") or "").strip()
    slots: dict = {"title": title, "date": date}
    if time_:
        slots["time"] = time_
    return slots


async def _extract_list_add(text: str) -> Optional[dict]:
    args = await _call_with_tool(text, _LIST_ADD_SCHEMA)
    if not args:
        return None
    return {
        "item": (args.get("item") or "").strip(),
        "list_type": args.get("list_type") or "shopping",
    }


async def _extract_note(text: str) -> Optional[dict]:
    args = await _call_with_tool(text, _NOTE_SCHEMA)
    if not args:
        return None
    title = ((args.get("title") or "").strip())[:60] or "Note"
    content = (args.get("content") or "").strip()
    return {"title": title, "content": content}


async def _extract_people(text: str) -> Optional[dict]:
    args = await _call_with_tool(text, _PEOPLE_SCHEMA)
    if not args:
        return None
    return {
        "name": (args.get("name") or "").strip(),
        "relationship": args.get("relationship") or "friend",
    }


async def _extract_people_fact(text: str) -> Optional[dict]:
    args = await _call_with_tool(text, _PEOPLE_FACT_SCHEMA)
    if not args:
        return None
    raw_date = (args.get("date") or "").strip()
    return {
        "person": (args.get("person") or "").strip(),
        "relationship": (args.get("relationship") or "").strip(),
        "attribute": (args.get("attribute") or "").strip().lower(),
        "value": (args.get("value") or "").strip(),
        "date": _normalize_date(raw_date) if raw_date else "",
    }


async def _extract_transaction(text: str) -> Optional[dict]:
    args = await _call_with_tool(text, _TRANSACTION_SCHEMA)
    if not args:
        return None
    # Produce exact integer cents plus the canonical two-decimal dollars the
    # command boundary expects. If the extracted amount doesn't parse to a valid
    # number, return None (caller falls through to Zoe Agent) rather than
    # recording a bogus $0 transaction.
    from money import to_cents, to_dollars
    try:
        cents = to_cents(args.get("amount") or 0)
    except ValueError:
        logger.warning("nlu_extractor: unparseable transaction amount %r", args.get("amount"))
        return None
    return {
        "description": (args.get("description") or "purchase").strip(),
        "amount": to_dollars(cents),
        "amount_cents": cents,
    }


async def _extract_journal(text: str) -> Optional[dict]:
    args = await _call_with_tool(text, _JOURNAL_SCHEMA)
    if not args:
        return None
    return {"content": (args.get("content") or "").strip()}


# ─── Public dispatcher ────────────────────────────────────────────────────────

_EXTRACTORS = {
    "calendar_create": _extract_calendar,
    "reminder_create": _extract_reminder,
    "list_add": _extract_list_add,
    "note_create": _extract_note,
    "people_create": _extract_people,
    "people_fact": _extract_people_fact,
    "transaction_create": _extract_transaction,
    "journal_create": _extract_journal,
}


async def extract_slots_for_intent(intent_name: str, text: str) -> Optional[dict]:
    """
    Extract structured slots for a create intent via the local LLM.

    Returns a dict of slot key/values compatible with intent_router._build_command,
    or None if extraction fails (caller should fall through to Zoe Agent).
    """
    fn = _EXTRACTORS.get(intent_name)
    if fn is None:
        logger.warning("nlu_extractor: no extractor for intent %s", intent_name)
        return None
    return await fn(text)
