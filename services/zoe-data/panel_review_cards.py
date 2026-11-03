"""Panel review cards: open loops ("what's on my plate?") and the day review card with new voices to name.

Flag-dark: ``ZOE_PANEL_REVIEW_CARDS`` (default OFF, read per call). Off = the voice tier returns before any work and the card
routes (``routers/panel_review_cards.py``) are ABSENT. Record: ``docs/knowledge/panel-review-cards.md``.

OPEN LOOPS (owner decision D5, 2026-10-18). Something the member said they would do, with no time on it, is an OPEN LOOP: no alarm,
nothing proactive. The store is the existing ``open_loops`` table (the nightly extractor, ``memory_digest._extract_open_loops``);
an approved pendant follow-up lands there too (``omi_review_apply.write_follow_up``, ``context='pendant:<candidate>'``, never
raised by the proactive engine). Voice first:

    "what's on my plate?" / "what did I say I'd do?"        reads them back (three named, the rest "on the screen") + the card
    "I've done the plumber one" / "tick off the plumber one" closes the one loop whose words cover "plumber"
    "drop the plumber one"                                   closes it as dropped
    "remind me about the plumber one tomorrow at 9"          makes a reminder (the existing writer) and closes the loop

A done / drop / remind shape whose words match NO loop returns None, so a list or reminder request is never stolen.

THE DAY REVIEW CARD ("review my day", "name the new voices"): the member's pending day-review candidates (Keep / Skip, at most
``MAX_CANDIDATES``) and at most ``MAX_VOICES`` (3) unknown pendant voices: Play (the owner-only clip, <= 8 s), That's me, someone
from their own people, TV / radio, Not now. Naming someone NEW needs typing, so it hands off to the phone (a QR to the Pendant page).

PRIVACY. Every read and write is the member's OWN rows (``user_id`` on every query, or the omi routes' own owner rules). The
member is the TURN's identity: the panel's voice session or a fresh PIN sign-in (``extra_ctx['scope_user']``), never a stale
binding; with none the reply is the private line and the panel gets the privacy-safe locked card. Health / money / grief /
family-conflict loops (``restraint``) are never read out or shown. The card reaches ONLY the panel's own push channel and carries
an opaque CARD TOKEN; a tap is honoured only while that member's voice session on that panel is still open, or when the request
carries that member's own session (a PIN sign-in). A token names its rows by opaque ids (``1``, ``c1``, ``v1``), so a tap can act
only on what was on screen. One live card per panel; ``CARD_TTL_S`` caps it regardless. Nothing is stored in the browser.
"""
from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_PANEL_REVIEW_CARDS"
TIER = "panel_review"
CARD_TTL_S = 600
MAX_LOOPS = 12
SPOKEN_LOOPS = 3
MAX_CANDIDATES = 5
MAX_VOICES = 3
MAX_PEOPLE = 8

PRIVATE_REPLY = "That's private. Say it again so I know it's you, or unlock with your PIN."
TITLES = {"open_loops": "On your plate", "day_review": "Your day"}
GROUPS = {"day": "From your day", "voices": "New voices"}
_KIND_LABEL = {"calendar_event": "Event", "list_item": "List", "reminder": "Reminder", "follow_up": "To do", "person": "Person",
               "owner_fact": "About you"}


def enabled() -> bool:
    from typed_env import env_bool

    return env_bool("ZOE_PANEL_REVIEW_CARDS", False)


# -- the words ---------------------------------------------------------------------------------------------------------------------
_LEAD = r"^\W*(?:(?:so|and|hey|ok|okay|um|uh|zoe|please|right|oh)[\s,]+)*"
_TAIL = r"(?:\s+(?:again|please|then|now|zoe|thanks))*\W*$"
_D = r"the\s+(?P<d>[\w' -]{2,40}?)\s+one"


def _rx(body: str) -> re.Pattern:
    return re.compile(_LEAD + body + _TAIL, re.IGNORECASE)


_SHAPES: tuple[tuple[str, re.Pattern], ...] = (
    ("read", _rx(r"what(?:['’]s|\s+is|s)\s+on\s+my\s+plate(?:\s+(?:today|this\s+week|right\s+now|at\s+the\s+moment))?")),
    ("read", _rx(r"what\s+(?:did|have)\s+i\s+(?:say|said|promise[sd]?)\s+(?:i(?:['’]d|\s+would|['’]ll|\s+will)\s+|to\s+)?do"
                 r"(?:\s+(?:today|this\s+week))?")),
    ("read", _rx(r"(?:what\s+are|show\s+me|read\s+me|tell\s+me|show|read)?\s*my\s+open\s+loops")),
    ("done", _rx(r"(?:i(?:['’]ve|\s+have)?\s+)?(?:done|did|finished|sorted(?:\s+out)?|handled|dealt\s+with|completed)\s+" + _D)),
    ("done", _rx(r"(?:mark|tick)\s+(?:off\s+)?" + _D + r"(?:\s+(?:as\s+)?(?:done|complete|completed|off))?")),
    ("done", _rx(_D + r"(?:['’]s|\s+is)\s+(?:done|sorted|finished|handled)")),
    ("drop", _rx(r"(?:drop|scrap|ditch|cross\s+off)\s+" + _D)),
    ("drop", _rx(r"i(?:['’]m|\s+am)\s+not\s+doing\s+" + _D + r"(?:\s+any\s*more)?")),
    ("remind", _rx(r"remind\s+me\s+(?:about|of|to\s+do)\s+" + _D + r"\s+(?P<w>.{2,40}?)")),
    ("remind", _rx(r"turn\s+" + _D + r"\s+into\s+a\s+reminder\s+(?:for\s+)?(?P<w>.{2,40}?)")),
    ("day", _rx(r"(?:let['’]?s\s+)?(?:review|go\s+through|do)\s+my\s+day(?:\s+review)?")),
    ("day", _rx(r"what\s+did\s+my\s+pendant\s+(?:hear|pick\s+up|catch)(?:\s+today)?")),
    ("day", _rx(r"(?:name|tag)\s+(?:the\s+|my\s+)?new\s+voices")),
    ("day", _rx(r"who\s+(?:are|were)\s+the\s+new\s+voices")),
)


def shape(text: str) -> Optional[tuple[str, str, str]]:
    """``(kind, descriptor, when)`` for a recognised utterance, else None. Whole-utterance anchored. Pure."""
    t = (text or "").strip()
    if not t or len(t) > 140:
        return None
    for kind, rx in _SHAPES:
        m = rx.match(t)
        if m:
            g = m.groupdict()
            return kind, (g.get("d") or "").strip(), (g.get("w") or "").strip()
    return None


_STOP = frozenset({"the", "and", "for", "that", "this", "with", "about", "one", "thing", "user", "their", "them", "they", "need",
                   "needs", "wants", "plans", "planning", "going", "said", "will", "would", "should", "his", "her", "jason"})


def _tokens(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", str(text or "").lower()) if w not in _STOP}


def _covers(desc: set[str], loop: set[str]) -> bool:
    return bool(desc) and all(any(t == u or (min(len(t), len(u)) >= 4 and (t.startswith(u) or u.startswith(t))) for u in loop)
                              for t in desc)


def match_loops(descriptor: str, loops: list[dict]) -> list[dict]:
    """The loops whose words cover every content word of ``descriptor`` ("plumber" -> "Call the plumber about the leak"). Pure."""
    desc = _tokens(descriptor)
    return [lp for lp in loops if _covers(desc, _tokens(lp["text"]))]


#: restraint classes never read out or shown on the wall (memories-space D11: the day review's own floor, plus feelings)
OFF_THE_WALL = frozenset({"health", "money", "grief", "family_conflict", "affect"})


def _private(text: str) -> bool:
    try:
        import restraint

        return bool(set(restraint.classify(text, head=False)) & OFF_THE_WALL)
    except Exception:  # noqa: BLE001 - a loop that cannot be classified stays off the wall (fail closed)
        return True


# -- the store ---------------------------------------------------------------------------------------------------------------------
def _ago(created: Any, now: datetime) -> str:
    try:
        dt = created if isinstance(created, datetime) else datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = (now - dt).days
    except Exception:  # noqa: BLE001
        return ""
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"


async def member_loops(member: str, *, wall: bool = True) -> list[dict]:
    """The member's open loops, newest first. ``wall`` (the panel and anything spoken aloud) leaves out the ones restraint keeps
    off the wall; a typed chat answer (``wall=False``) lists them all, so nothing the member approved is unreachable."""
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        async with db.execute("SELECT id, loop_text, created_at FROM open_loops WHERE user_id = ? AND resolved IS NOT TRUE "
                              "ORDER BY created_at DESC LIMIT 40", (member,)) as cur:
            rows = await cur.fetchall()
    now = datetime.now(timezone.utc)
    out = [{"id": r[0], "text": " ".join(str(r[1] or "").split())[:200], "ago": _ago(r[2], now)} for r in rows]
    return [lp for lp in out if lp["text"] and not (wall and _private(lp["text"]))][:MAX_LOOPS]


async def close_loop(member: str, loop_id: Any, how: str) -> bool:
    """Resolve one of the member's loops (``how`` = done | dropped | reminder, logged as a count). Also expires its proactive
    candidate so it is not raised later. False when it is not theirs or already closed."""
    from db_compat import get_compat_db

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with get_compat_db() as db:
        cur = await db.execute("UPDATE open_loops SET resolved = TRUE, resolved_at = CURRENT_TIMESTAMP "
                               "WHERE id = ? AND user_id = ? AND resolved IS NOT TRUE", (loop_id, member))
        ok = bool(getattr(cur, "rowcount", 0))
        if ok:
            try:
                await db.execute("UPDATE proactive_candidates SET expires_at = ? WHERE user_id = ? AND source_ref = ? AND expires_at > ?",
                                 (stamp, member, f"open_loops:{loop_id}", stamp))
            except Exception as exc:  # noqa: BLE001 - the loop is closed either way
                logger.debug("panel_review: candidate expiry skipped: %s", type(exc).__name__)
        await db.commit()
    logger.info("PANEL_REVIEW loop_closed=%d how=%s", int(ok), how)
    return ok


async def loop_to_reminder(member: str, loop: dict, due_date: str, due_time: str) -> bool:
    """A reminder through the existing writer (role check, notification, scheduling), then the loop is closed."""
    from db_compat import get_compat_db
    from models import ReminderCreate
    from reminder_service import create_reminder_record

    async with get_compat_db() as db:
        cur = await db.execute("SELECT role FROM users WHERE id = ?", (member,))
        row = await cur.fetchone()
        made = await create_reminder_record(
            ReminderCreate(title=loop["text"][:120], description="From your open loops.", due_date=due_date or None,
                           due_time=due_time or None),
            user={"user_id": member, "role": (row[0] if row else None) or "user"}, db=db)
        await db.commit()
    if not str((made or {}).get("id") or ""):
        return False
    await close_loop(member, loop["id"], "reminder")
    return True


# -- when ----------------------------------------------------------------------------------------------------------------------------
def _household_now() -> datetime:
    from proactive.triggers.reminder_scan import zoe_now

    return zoe_now(None)


def presets(now: Optional[datetime] = None) -> list[tuple[str, str, str, str]]:
    """``(key, label, date, time)`` choices for "Remind me" by touch, in household time. Pure but for the clock."""
    now = now or _household_now()
    out = []
    if now.hour < 17:
        out.append(("evening", "This evening, 6 PM", now.date().isoformat(), "18:00"))
    out.append(("tomorrow", "Tomorrow, 9 AM", (now + timedelta(days=1)).date().isoformat(), "09:00"))
    sat = now + timedelta(days=(5 - now.weekday()) % 7 or 7)
    out.append(("saturday", f"Saturday {sat.day}, 10 AM", sat.date().isoformat(), "10:00"))
    return out


def parse_when(words: str, now: Optional[datetime] = None) -> Optional[tuple[str, str]]:
    """``(YYYY-MM-DD, HH:MM)`` from "tomorrow at 9", "at 5pm", "friday", else None. A time already past today means tomorrow; a
    date with no time is 9 AM. Never a guess: a phrase the reminder grammar does not know is None."""
    now = now or _household_now()
    raw = " ".join(str(words or "").lower().split())
    hhmm = None
    m = (re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d\d))?\s*([ap])\.?m\.?(?!\w)", raw)
         or re.search(r"\bat\s+(\d{1,2})(?::(\d\d))?()(?!\w)", raw))
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "")
        if ap:
            if not 1 <= h <= 12:
                return None
            h = h % 12 + (12 if ap == "p" else 0)
        if h > 23 or mi > 59:
            return None
        hhmm = f"{h:02d}:{mi:02d}"
        raw = (raw[:m.start()] + raw[m.end():]).strip()
    raw = re.sub(r"\b(?:at|on|for|this)\b", " ", raw).strip(" ,.")
    raw = " ".join(raw.split())
    day = None
    if raw:
        try:
            from reminder_service import normalize_due_date

            day = normalize_due_date(raw, now_utc=now.astimezone(timezone.utc))
        except Exception:  # noqa: BLE001 - not a date the reminder grammar knows
            return None
    if not hhmm and not day:
        return None
    if not day:
        today = now.date()
        past = hhmm <= now.strftime("%H:%M")
        day = (today + timedelta(days=1) if past else today).isoformat()
    return day, hhmm or "09:00"


def _spoken_when(day: str, hhmm: str, now: Optional[datetime] = None) -> str:
    now = now or _household_now()
    h, mi = int(hhmm[:2]), int(hhmm[3:5])
    clock = f"{(h % 12) or 12}{':%02d' % mi if mi else ''} {'AM' if h < 12 else 'PM'}"
    d = datetime.fromisoformat(day).date()
    name = "today" if d == now.date() else "tomorrow" if d == now.date() + timedelta(days=1) else d.strftime("%A")
    return f"{name} at {clock}"


# -- card tokens -----------------------------------------------------------------------------------------------------------------
@dataclass
class Card:
    token: str
    panel_id: str
    member_id: str
    kind: str
    items: dict = field(default_factory=dict)      # opaque id -> {"type", "ref", ...}
    choices: dict = field(default_factory=dict)    # remind preset key -> (date, time, label)
    people: frozenset = frozenset()
    expires: float = 0.0


_CARDS: dict[str, Card] = {}


class CardError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def mint(panel_id: str, member_id: str, kind: str, items: dict, *, choices: Optional[dict] = None, people=()) -> str:
    """A card token for ``panel_id``; every older card of that panel is void (one live card per panel)."""
    now = time.monotonic()
    for tok in [t for t, c in _CARDS.items() if c.panel_id == panel_id or c.expires < now]:
        _CARDS.pop(tok, None)
    tok = secrets.token_urlsafe(24)
    _CARDS[tok] = Card(tok, panel_id, member_id, kind, dict(items), dict(choices or {}), frozenset(people), now + CARD_TTL_S)
    return tok


def authorize(token: str, caller: Optional[dict]) -> Card:
    """The card a tap may act on: it exists, it has not expired, and EITHER that member's voice session on that panel is still
    open OR the request carries that member's own session. Anything else is refused (404 unknown, 403 not the member)."""
    card = _CARDS.get(str(token or ""))
    if card is None or card.expires < time.monotonic():
        _CARDS.pop(str(token or ""), None)
        raise CardError(404, "card_gone")
    try:
        import voice_session

        if voice_session.active_member(card.panel_id) == card.member_id:
            return card
    except Exception:  # noqa: BLE001
        pass
    uid = str((caller or {}).get("user_id") or "")
    if uid and uid == card.member_id and "device_token_role" not in (caller or {}) and not (caller or {}).get("auth_degraded"):
        return card
    raise CardError(403, "not_your_card")


def reset() -> None:
    _CARDS.clear()


def _member(user_id: str, channel: str, extra_ctx: Optional[dict]) -> Optional[str]:
    ctx = extra_ctx or {}
    raw = ctx.get("scope_user") if (channel == "voice" and "scope_user" in ctx) else user_id
    m = str(raw or "").strip()
    if not m or m.startswith("panel"):
        return None
    try:
        import identity_facts
        from user_filters import is_unidentified

        if is_unidentified(m) or not identity_facts._is_account_id(m):
            return None
    except Exception:  # noqa: BLE001
        return None
    return m


# -- the cards ---------------------------------------------------------------------------------------------------------------------
def _frame(panel_id: str, member: str, kind: str, token: str, items: list, *, subtitle: str = "", empty: str = "") -> dict:
    return {"panel_id": panel_id, "member_id": member, "kind": kind, "private": True, "title": TITLES[kind], "subtitle": subtitle,
            "items": items, "more": 0, "empty": empty, "card_token": token, "groups": GROUPS}


def loops_card(panel_id: str, member: str, loops: list[dict]) -> dict:
    choices = {k: (d, t, label) for k, label, d, t in presets()}
    remind = {"key": "remind", "label": "Remind me", "choices": [{"key": k, "label": v[2]} for k, v in choices.items()]}
    reg, items = {}, []
    for n, lp in enumerate(loops, 1):
        reg[str(n)] = {"type": "loop", "ref": lp["id"], "text": lp["text"]}
        items.append({"id": str(n), "text": lp["text"], "meta": lp["ago"],
                      "actions": [{"key": "done", "label": "Done"}, remind, {"key": "drop", "label": "Drop"}]})
    tok = mint(panel_id, member, "open_loops", reg, choices=choices)
    return _frame(panel_id, member, "open_loops", tok, items, subtitle="Say “I've done the … one”, or tap.",
                  empty="Nothing on your plate.")


def _candidate_item(c: dict) -> tuple[str, str]:
    p = c.get("payload") or {}
    text = next((str(p.get(k)) for k in ("title", "item", "name", "fact", "text") if p.get(k)), "")
    when = " ".join(str(p.get(k)) for k in ("date", "time") if p.get(k))
    return " ".join(text.split())[:160], " ".join(x for x in (_KIND_LABEL.get(str(c.get("kind")), "Item"), when) if x)


async def day_parts(member: str) -> tuple[list[dict], list[dict], list[dict]]:
    """``(candidates, voices, people)`` the member may see: each through its own route's owner rule; a part whose feature is off
    or whose rule refuses the member is empty."""
    from fastapi import HTTPException

    user = {"user_id": member, "role": "user", "username": member}
    cands: list = []
    voices: list = []
    people: list = []
    try:
        import omi_day_review as dr
        from routers import omi_day_review as dr_router

        if dr.enabled():
            await dr_router._require_member(user)
            cands = [c for c in await dr.list_candidates(member, status="pending", limit=MAX_CANDIDATES)]
    except HTTPException:
        cands = []
    except Exception as exc:  # noqa: BLE001
        logger.debug("panel_review: candidates unavailable: %s", type(exc).__name__)
    try:
        from typed_env import env_bool

        if env_bool("ZOE_OMI_SYNC_ENABLED", False):
            from routers import omi_voices as ov

            await ov._require_owner(user)
            voices = ((await ov.unknown_voices(user)) or {}).get("voices", [])[:MAX_VOICES]
            if voices:
                people = ((await ov.my_people(user)) or {}).get("people", [])[:MAX_PEOPLE]
    except HTTPException:
        voices, people = [], []
    except Exception as exc:  # noqa: BLE001
        logger.debug("panel_review: voices unavailable: %s", type(exc).__name__)
    return cands, voices, people


def day_card(panel_id: str, member: str, cands: list, voices: list, people: list) -> dict:
    reg, items = {}, []
    for n, c in enumerate(cands, 1):
        text, meta = _candidate_item(c)
        reg[f"c{n}"] = {"type": "candidate", "ref": c["id"]}
        items.append({"id": f"c{n}", "group": "day", "text": text, "meta": meta,
                      "actions": [{"key": "keep", "label": "Keep"}, {"key": "skip", "label": "Skip"}]})
    picks = [{"key": f"p{i}", "label": p["name"]} for i, p in enumerate(people, 1)] + [
        {"key": "new", "label": "Someone new", "phone": True}]
    for n, v in enumerate(voices, 1):
        sample = next((s for s in v.get("samples") or [] if s.get("has_audio")), None)
        reg[f"v{n}"] = {"type": "voice", "ref": v["id"], "segment": (sample or {}).get("segment_id")}
        acts = ([{"key": "play", "label": "Play"}] if sample else []) + [
            {"key": "me", "label": "That's me"}, {"key": "tag", "label": "Someone I know", "choices": picks},
            {"key": "media", "label": "TV / radio"}, {"key": "later", "label": "Not now", "local": True}]
        meta = f"heard in {v.get('recordings', 0)} recording{'s' if v.get('recordings') != 1 else ''}"
        items.append({"id": f"v{n}", "group": "voices", "text": str(v.get("name") or "A voice"), "meta": meta, "actions": acts})
    pmap = {f"p{i}": p["id"] for i, p in enumerate(people, 1)}
    tok = mint(panel_id, member, "day_review", reg, choices=pmap, people=pmap.values())
    return _frame(panel_id, member, "day_review", tok, items, empty="Nothing from your day is waiting.")


async def _push(frame: Optional[dict]) -> int:
    try:
        import voice_cards

        return await voice_cards.push(frame)
    except Exception:  # noqa: BLE001
        return 0


# -- the voice / chat tier ---------------------------------------------------------------------------------------------------------
def _listed(texts: list[str]) -> str:
    texts = [t.rstrip(".") for t in texts]
    return texts[0] if len(texts) == 1 else ", ".join(texts[:-1]) + " and " + texts[-1]


async def answer(text: str, user_id: str, *, channel: str, extra_ctx: Optional[dict], allow_writes: bool) -> Optional[str]:
    sh = shape(text)
    if sh is None:
        return None
    kind, desc, when = sh
    ctx = extra_ctx or {}
    panel_id = str(ctx.get("panel_id") or "") if channel == "voice" else ""
    member = _member(user_id, channel, ctx)
    if member is None:
        if kind in ("done", "drop", "remind"):
            return None                                   # not addressed to a loop we could check: never stolen
        if panel_id:
            try:
                import voice_cards

                await voice_cards.push(voice_cards.locked(panel_id))
            except Exception:  # noqa: BLE001
                pass
        return PRIVATE_REPLY
    if kind == "day":
        try:                                              # the member took the day review up: the raise's "ignored" count resets
            import day_review_raise

            await day_review_raise.note_engaged(member, dry=not allow_writes)
        except Exception:  # noqa: BLE001
            pass
        cands, voices, people = await day_parts(member)
        if panel_id:
            await _push(day_card(panel_id, member, cands, voices, people))
        if not cands and not voices:
            return "Nothing from your day is waiting."
        bits = ([f"{len(cands)} thing{'s' if len(cands) != 1 else ''} from your day"] if cands else []) + (
            [f"{len(voices)} new voice{'s' if len(voices) != 1 else ''} to name"] if voices else [])
        return f"You've got {' and '.join(bits)}." + (" They're on the screen." if panel_id else "")
    loops = await member_loops(member, wall=channel == "voice")
    if kind == "read":
        if panel_id:
            await _push(loops_card(panel_id, member, loops))
        if not loops:
            return "Nothing's on your plate. No open loops."
        head = f"You've got {len(loops)} open loop{'s' if len(loops) != 1 else ''}: "
        said = _listed([lp["text"] for lp in loops[:SPOKEN_LOOPS]])
        more = len(loops) - SPOKEN_LOOPS
        return head + said + (f", and {more} more on the screen." if more > 0 and panel_id else f", and {more} more." if more > 0 else ".")
    hits = match_loops(desc, loops)
    if not hits:
        return None
    if len(hits) > 1:
        return f"That could be {_listed([h['text'] for h in hits[:3]])}. Which one?"
    loop = hits[0]
    if kind == "remind":
        due = parse_when(when)
        if due is None:
            return f"When should I remind you? Say something like “remind me about the {desc} one tomorrow at 9”."
        try:
            made = await loop_to_reminder(member, loop, *due) if allow_writes else True
        except Exception as exc:  # noqa: BLE001 - a refused or failed write is said, never a fall-through
            logger.info("panel_review: reminder refused: %s", type(exc).__name__)
            made = False
        if not made:
            return "I couldn't set that reminder."
        return f"Okay, I'll remind you {_spoken_when(*due)}: {loop['text'].rstrip('.')}."
    if allow_writes:
        await close_loop(member, loop["id"], "done" if kind == "done" else "dropped")
        if panel_id:                                      # the card on the wall shows what is left
            await _push(loops_card(panel_id, member, await member_loops(member)))
    return (f"Ticked off: {loop['text'].rstrip('.')}." if kind == "done" else f"Dropped: {loop['text'].rstrip('.')}.")


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
               extra_ctx: Optional[dict[str, Any]] = None, allow_writes: bool = True):
    """The fast-tier hook (voice and chat): a ``DispatchResult`` or None. Off = None before any work. NEVER raises."""
    try:
        ch = (channel or "").strip().lower()
        if ch not in ("voice", "chat") or not enabled():
            return None
        reply = await answer(text, user_id, channel=ch, extra_ctx=extra_ctx, allow_writes=allow_writes)
        if not reply:
            return None
        import expert_dispatch as _xd

        logger.info("PANEL_REVIEW answered=1 channel=%s private=%d", ch, int(reply == PRIVATE_REPLY))
        return _xd.DispatchResult(domain="memory", reply=reply, intent="open_loops", tier=TIER)
    except Exception as exc:  # noqa: BLE001
        logger.warning("panel_review: tier failed (non-fatal): %s", type(exc).__name__)
        return None
