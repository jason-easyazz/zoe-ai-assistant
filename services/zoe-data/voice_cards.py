"""Private result cards for a SPOKEN read (flag-dark with ``ZOE_VOICE_SESSION``).

Owner requirement (2026-10-13): "if I ask what is on my calendar, I want to see the results as well as hear them".
On the live config (``ZOE_SKYBRIDGE_ONLY=true``) a voice read was speech-only on the panel: the estate does not render
Skybridge cards (a ``show_card`` puts only the summary text in the dock), the legacy ``_broadcast_*_ui`` helpers are
gated off, and the estate screen a voice turn lands on fetches with the panel's own (guest) session. Diagnosis +
record: ``docs/knowledge/voice-verified-session.md``.

This module turns a read the turn is AUTHORISED for (the member's voice session, or a fresh PIN sign-in) into one
``voice_card`` push frame carrying the real rows - the events, the list items, the reminders - and sends it to THAT
panel's own channel only (``voice_session.send_to_panel``; never the 'all' fallback). A turn that is not authorised gets
a privacy-safe ``locked`` card instead ("Say it again so I know it's you, or unlock with your PIN."). Frames carry the
member id so the estate clears them when that member's session closes.

Frame (``voice_card``)::

    {"panel_id", "member_id", "kind": "calendar"|"lists"|"reminders"|"locked", "private": bool,
     "title", "subtitle", "items": [{"text", "meta", "done"}], "more": int, "empty", "body"}

No English in the code: titles, empty lines and the spoken calendar read-back come from the lexicon's
``voice_session`` section. Never raises (a card is an addition to the spoken answer, not a condition of it).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

import voice_session
from skybridge_service import _spoken_clock

logger = logging.getLogger(__name__)

MAX_ITEMS = 12


def _card_contents(result: Optional[dict]) -> list:
    out = []
    for card in (result or {}).get("cards") or []:
        if not isinstance(card, dict):
            continue
        content = card.get("content") if isinstance(card.get("content"), dict) else card.get("props")
        if isinstance(content, dict):
            out.append(content)
    return out


def _clock(hhmm: Any) -> str:
    """How a stored time is SAID ("09:00" -> "9 AM"): the Skybridge read-back's own formatter, so card and speech agree."""
    return _spoken_clock(str(hhmm or "")) if hhmm else ""


def _event_item(ev: dict, *, multi_day: bool) -> dict:
    title = str(ev.get("title") or ev.get("summary") or "").strip()
    when = "" if ev.get("all_day") else _clock(ev.get("start_time"))
    day = ""
    if multi_day:
        try:
            day = date.fromisoformat(str(ev.get("start_date"))[:10]).strftime("%a %d")
        except Exception:
            day = str(ev.get("start_date") or "")[:10]
    return {"text": title, "meta": " ".join(p for p in (day, when) if p)}


def _frame(panel_id: str, member_id: str, kind: str, *, subtitle: str, items: list, total: int) -> dict:
    titles = voice_session.text_for("card_titles", default={}) or {}
    empties = voice_session.text_for("card_empty", default={}) or {}
    return {
        "panel_id": panel_id,
        "member_id": member_id,
        "kind": kind,
        "private": True,
        "title": str(titles.get(kind) or kind.title()),
        "subtitle": subtitle,
        "items": items[:MAX_ITEMS],
        "more": max(0, total - MAX_ITEMS),
        "empty": str(empties.get(kind) or ""),
    }


def from_skybridge(panel_id: str, member_id: str, result: Optional[dict]) -> Optional[dict]:
    """The card for a handled Skybridge calendar / list READ, built from the rows its own cards already carry."""
    for content in _card_contents(result):
        source = content.get("source")
        if source == "calendar_show":
            events = [e for e in (content.get("events") or []) if isinstance(e, dict)]
            multi = str(content.get("start_date") or "") != str(content.get("end_date") or content.get("start_date") or "")
            items = [_event_item(e, multi_day=multi) for e in events]
            return _frame(panel_id, member_id, "calendar", subtitle=str(content.get("qualifier") or ""),
                          items=items, total=len(items))
        if source == "list_show":
            raw = [i for i in (content.get("items") or []) if isinstance(i, dict)]
            items = [{"text": str(i.get("text") or i.get("title") or "").strip(), "meta": "",
                      "done": bool(i.get("completed"))} for i in raw]
            return _frame(panel_id, member_id, "lists", subtitle=str(content.get("list_name") or ""),
                          items=items, total=len(items))
    return None


async def for_read(panel_id: str, member_id: str, kind: str, text: str) -> Optional[dict]:
    """The card for a private read answered OUTSIDE Skybridge (the intent lane): the same rows, read for ``member_id``."""
    try:
        if kind in {"calendar", "lists"}:
            import skybridge_service as sky

            intent = sky.classify_skybridge_intent(text, None)
            if intent is None or intent.domain != kind or voice_session.private_read_kind(
                    skybridge=(intent.domain, intent.action)) != kind:
                return None   # only a READ is ever resolved here: a write-shaped utterance must not run twice
            return from_skybridge(panel_id, member_id, await sky.resolve_skybridge_request(text, member_id))
        if kind == "reminders":
            from database import get_db_ctx

            async with get_db_ctx() as db:
                cursor = await db.execute(
                    "SELECT title, due_date, due_time FROM reminders"
                    " WHERE (visibility = 'family' OR user_id = ?)"
                    " AND is_active = 1 AND deleted = 0 ORDER BY due_date, due_time LIMIT 20",
                    (member_id,),
                )
                rows = [dict(r) for r in await cursor.fetchall()]
            items = [{"text": str(r.get("title") or "").strip(),
                      "meta": " ".join(p for p in (str(r.get("due_date") or "")[:10], _clock(r.get("due_time"))) if p)}
                     for r in rows]
            return _frame(panel_id, member_id, "reminders", subtitle="", items=items, total=len(items))
    except Exception as exc:
        logger.debug("voice card (%s) unavailable: %s", kind, exc)
    return None


def locked(panel_id: str, *, step_up: bool = False) -> dict:
    """The privacy-safe card for a private ask the turn is NOT authorised for: no member, no rows. It offers the voice
    route only when a voice session could actually open (the model gate); otherwise just the PIN."""
    titles = voice_session.text_for("card_titles", default={}) or {}
    if step_up:
        body = voice_session.text_for("card_step_up")
    else:
        voice_ok, _ = voice_session.model_gate({"voice_model_id": voice_session.acting_model()})
        body = voice_session.text_for("card_locked_voice" if voice_ok else "card_locked_pin")
    return {"panel_id": panel_id, "kind": "locked", "private": False,
            "title": str(titles.get("locked") or ""), "body": str(body or ""), "items": []}


async def push(frame: Optional[dict]) -> int:
    """Send a card to its panel's own channel. 0 = not delivered (no live socket there, or no frame)."""
    if not frame or not frame.get("panel_id"):
        return 0
    return await voice_session.send_to_panel(str(frame["panel_id"]), "voice_card", frame)


def _when_phrase(qualifier: str, tpl: dict) -> str:
    """"today" / "tomorrow" / "this week" stand alone; a named day or a date takes the lexicon's preposition ("on friday")."""
    q = str(qualifier or "").strip()
    bare = {str(w).lower() for w in tpl.get("bare_qualifiers") or ()}
    if not q or q.lower() in bare:
        return q
    return str(tpl.get("on") or "{qualifier}").format(qualifier=q)


def spoken_events(events: list, qualifier: str, *, start_date: str = "", end_date: str = "") -> Optional[str]:
    """Name the events aloud, briefly: at most ``max_named`` (3) with time + title, the rest as "and N more on screen". For
    a read the turn is AUTHORISED for only - the caller never passes a guest's (there are none) rows. A multi-day range names
    each event's weekday. None when the lexicon has no templates (the caller keeps its count)."""
    tpl = voice_session.text_for("spoken_calendar", default={}) or {}
    if not tpl:
        return None
    when = _when_phrase(qualifier, tpl)
    fmt = {"qualifier": qualifier, "when": when}
    events = [e for e in (events or []) if isinstance(e, dict)]
    if not events:
        return str(tpl.get("none") or "").format(**fmt) or None
    try:
        cap = max(1, int(tpl.get("max_named") or 3))
    except (TypeError, ValueError):
        cap = 3
    multi = bool(start_date) and str(start_date)[:10] != str(end_date or start_date)[:10]
    named = []
    for ev in events[:cap]:
        title = str(ev.get("title") or ev.get("summary") or "").strip() or "?"
        clock = "" if ev.get("all_day") else _clock(ev.get("start_time"))
        day = ""
        if multi:
            try:
                day = date.fromisoformat(str(ev.get("start_date"))[:10]).strftime("%A")
            except Exception:
                day = ""
        key = "item_" + ("timed" if clock else "untimed") + ("_day" if day else "")
        named.append(str(tpl.get(key) or tpl.get("item_untimed") or "{title}").format(title=title, time=clock, day=day))
    if len(events) > cap:
        named.append(str(tpl.get("more") or "").format(n=len(events) - cap))
    joiner, last = str(tpl.get("joiner") or ", "), str(tpl.get("last_joiner") or " and ")
    listed = named[0] if len(named) == 1 else joiner.join(named[:-1]) + last + named[-1]
    key = "one" if len(events) == 1 else "many"
    return str(tpl.get(key) or "").format(count=len(events), list=listed, **fmt) or None


def spoken_private() -> Optional[str]:
    """What a turn with NO identity hears for a calendar read: no count, no title, only how to unlock."""
    return str(voice_session.text_for("spoken_calendar", default={}).get("private") or "") or None


def spoken_calendar(result: Optional[dict]) -> Optional[str]:
    """``spoken_events`` for a handled Skybridge calendar read (its ``calendar_show`` card); None for any other result."""
    for content in _card_contents(result):
        if content.get("source") != "calendar_show":
            continue
        return spoken_events(content.get("events") or [], str(content.get("qualifier") or "").strip(),
                             start_date=str(content.get("start_date") or ""), end_date=str(content.get("end_date") or ""))
    return None
