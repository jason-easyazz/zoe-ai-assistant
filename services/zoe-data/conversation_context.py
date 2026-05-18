from __future__ import annotations
import re, time
from dataclasses import dataclass, field
from typing import ClassVar, Optional

# Note: no trailing \b after % — "%" is non-word so \b never matches after it at end-of-string.
_PERCENTAGE_RE   = re.compile(r'\b(\d{1,3})\s*(?:percent\b|%)|(?:^|\s)(\d{1,3})(?:\s|$)', re.I)
_DIRECTION_RE    = re.compile(r'\b(up|louder|higher|down|quieter|lower|softer)\b', re.I)
_CANCEL_RE       = re.compile(r'\b(cancel|delete|remove|dismiss|clear)\b', re.I)
_DONE_RE         = re.compile(r'\b(done|completed?|finished?|mark.*done|tick)\b', re.I)
_SHOW_RE         = re.compile(r'\b(show|list|open|display|see|view|what)\b', re.I)
# Date/time extractor for follow-up calendar/reminder edits
_DATE_RE         = re.compile(
    r'\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday'
    r'|\d{1,2}(?:st|nd|rd|th)?(?:\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*)?'
    r'|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b',
    re.I,
)
_TIME_RE         = re.compile(r'\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)|noon|midnight)\b', re.I)


@dataclass
class ConversationContext:
    last_intent: str | None = None
    last_slots:  dict       = field(default_factory=dict)
    last_text:   str | None = None
    updated_at:  float      = field(default_factory=time.time)
    TTL: ClassVar[float]    = 120.0  # 2-minute window

    def is_fresh(self) -> bool:
        return (time.time() - self.updated_at) < self.TTL

    def activate(self, intent_name: str, slots: dict, text: str) -> None:
        self.last_intent = intent_name
        self.last_slots  = dict(slots)
        self.last_text   = text
        self.updated_at  = time.time()

    def invalidate(self) -> None:
        self.last_intent = None

    def resolve_coreference(self, text: str) -> tuple[str | None, dict | None]:
        """
        Try to resolve an ambiguous utterance relative to the last intent.
        Returns (intent_name, slots) or (None, None).

        Covers: volume, music, calendar events, reminders, and list items.
        """
        if not self.is_fresh() or not self.last_intent:
            return None, None

        t = text.strip().lower()

        # ── Volume ────────────────────────────────────────────────────────────
        if self.last_intent == "set_volume":
            m = _PERCENTAGE_RE.search(text)
            if m:
                return "set_volume", {"level": int(m.group(1) or m.group(2))}
            d = _DIRECTION_RE.search(text)
            if d:
                raw = d.group(1).lower()
                normalized = "up" if raw in ("up", "louder", "higher") else "down"
                return "set_volume", {"direction": normalized}

        # ── Music ─────────────────────────────────────────────────────────────
        if self.last_intent in ("music_play", "music_control", "music_volume"):
            m = _PERCENTAGE_RE.search(text)
            if m:
                return "music_volume", {"level": int(m.group(1) or m.group(2))}
            if _CANCEL_RE.search(t):
                return "music_stop", {}

        # ── Calendar follow-ups ────────────────────────────────────────────────
        # "actually make it 3pm" / "change that to Friday" after a calendar_add
        if self.last_intent in ("calendar_add", "calendar_create_event"):
            date_m = _DATE_RE.search(text)
            time_m = _TIME_RE.search(text)
            if date_m or time_m:
                new_slots = dict(self.last_slots)
                if date_m:
                    new_slots["date"] = date_m.group(0)
                if time_m:
                    new_slots["time"] = time_m.group(0)
                return "calendar_add", new_slots
            if _CANCEL_RE.search(t):
                event_id = self.last_slots.get("event_id") or self.last_slots.get("title", "")
                return "calendar_delete_event", {"title": event_id}
            if _SHOW_RE.search(t):
                return "calendar_list_events", {}

        # ── Reminder follow-ups ────────────────────────────────────────────────
        # "actually remind me at 9am" / "cancel that reminder" after reminder_create
        if self.last_intent in ("reminder_create", "timer_set"):
            time_m = _TIME_RE.search(text)
            if time_m:
                new_slots = dict(self.last_slots)
                new_slots["time"] = time_m.group(0)
                return self.last_intent, new_slots
            if _CANCEL_RE.search(t):
                return "reminder_cancel", {"title": self.last_slots.get("title", "")}
            if _SHOW_RE.search(t):
                return "reminder_list", {}

        # ── List follow-ups ────────────────────────────────────────────────────
        # "remove that" / "mark it done" / "show the list" after list_add
        if self.last_intent in ("list_add", "list_add_item"):
            if _CANCEL_RE.search(t) or _DONE_RE.search(t):
                return "list_remove_item", {"item": self.last_slots.get("item", "last item")}
            if _SHOW_RE.search(t):
                return "list_get_items", {"list_name": self.last_slots.get("list_name", "shopping")}

        return None, None
