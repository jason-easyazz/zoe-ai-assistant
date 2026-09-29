"""Brief on the first turn of the day — flag-dark (``ZOE_BRIEF_ON_FIRST_TURN``).

No unprompted spoken brief: on a real member's first brain turn of the morning,
the day's context rides into THAT turn's prompt as a ``[Today]`` block and Zoe
mentions it naturally. Both brain lanes call ``prepare`` (block or None, never
raises) and ``settle`` (takes the day's SHARED ``proactive.arrival`` claim only
once the lane produced a reply). Emptiness, the window and greeting-vs-command
are decided here in code, never by the model. Full contract:
docs/knowledge/synthetic-users-and-proactive-recipients.md.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from conversation_opener import _normalize, is_conversation_opener
from time_utils import zoe_timezone
from user_filters import is_synthetic_user

logger = logging.getLogger(__name__)

TRIGGER_TYPE = "brief_first_turn"
# Composition-owned delimiters, whole lines. The core seam registers this pair in
# `_CONTEXT_BLOCKS` (mirrored in zoe-core memory.ts) so superseded copies elide.
BLOCK_LABEL = "[Today]"
BLOCK_CLOSE = "[END Today]"

GREETING_INSTRUCTION = (
    "What is on for this user today. Mention it naturally, once, like a human "
    "assistant would; do not read it out as a list. If they asked for something "
    "specific, answer that first and weave in only what is time-relevant."
)
COMMAND_INSTRUCTION = (
    "The user asked for something specific: do that and answer it first. Then add "
    "at most ONE short line about this, because it is soon."
)

_TRUTHY = {"1", "true", "yes", "on"}
_SOON = timedelta(hours=2)
_PREPARE_TIMEOUT_S = 2.0
_GATHER_CACHE_S = 300.0  # a task added mid-morning shows up within 5 min
_ITEM_MAX_CHARS = 140
_HHMM_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})")

# In-process throttles only; once-per-day correctness rests on the claim row.
_settled: dict[str, str] = {}  # user -> local date whose claim is known taken
_ctx_cache: dict[str, tuple[str, float, dict]] = {}


def brief_on_first_turn_enabled() -> bool:
    """``ZOE_BRIEF_ON_FIRST_TURN`` — default OFF, read per call."""
    return (os.environ.get("ZOE_BRIEF_ON_FIRST_TURN", "") or "").strip().lower() in _TRUTHY


def _hhmm_minutes(raw: str | None, default: str) -> int:
    match = _HHMM_RE.match(raw or "") or _HHMM_RE.match(default)
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 24 or minutes > 59:
        match = _HHMM_RE.match(default)
        hours, minutes = int(match.group(1)), int(match.group(2))
    return hours * 60 + minutes


def in_window(local: datetime) -> bool:
    start = _hhmm_minutes(os.environ.get("ZOE_BRIEF_WINDOW_START", "05:00"), "05:00")
    end = _hhmm_minutes(os.environ.get("ZOE_BRIEF_WINDOW_END", "12:00"), "12:00")
    return start <= local.hour * 60 + local.minute < end


# ── Greeting vs command (phrase-gated, conversation_opener style) ────────────
_GREETING_LEADS = (
    "good morning", "morning", "hey there", "hi there", "hello there", "hello", "hey", "hi",
    "hiya", "gday", "g day", "howdy", "yo", "ok", "okay", "well", "so", "oh",
)
_OPEN_PHRASES = frozenset({
    "whats up", "sup", "hows it going", "how is it going", "how are you",
    "how are you doing", "how are you today", "how are things", "hows things",
    "hows your morning", "how was your night", "whats new", "whats happening",
    "whats going on", "whats on", "whats on today", "whats on for today",
    "what have i got today", "what do i have today", "what does my day look like",
    "whats my day look like", "whats the plan", "whats the plan today",
    "anything i need to know", "anything on today",
})


def turn_shape(message: str | None) -> str:
    """``greeting`` for a greeting/open turn, else ``command``. Pure, no model.

    The whole utterance must be greeting words and/or one open phrase ("morning
    zoe, how's it going?"), or a let's-talk opener; anything with a request in
    it ("morning, turn on the lights") is a command.
    """
    if is_conversation_opener(message):
        return "greeting"
    text = " ".join(t for t in _normalize(message).split() if t != "zoe")
    if not text:
        return "command"
    stripped = True
    while stripped and text:
        stripped = False
        for lead in _GREETING_LEADS:
            if text == lead or text.startswith(lead + " "):
                text = text[len(lead):].strip()
                stripped = True
                break
    return "greeting" if not text or text in _OPEN_PHRASES else "command"


# ── Day items (deterministic) ────────────────────────────────────────────────
def _clean(text) -> str:
    """Stored content flattened to one line, bracket-free (cannot forge a
    delimiter), capped."""
    text = re.sub(r"[\[\]]", "", re.sub(r"\s+", " ", str(text or ""))).strip()
    return text if len(text) <= _ITEM_MAX_CHARS else text[:_ITEM_MAX_CHARS].rsplit(" ", 1)[0] + "…"


def _at(local_now: datetime, hhmm) -> datetime | None:
    match = _HHMM_RE.match(str(hhmm or ""))
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return local_now.replace(hour=int(match.group(1)), minute=int(match.group(2)),
                             second=0, microsecond=0)


def _as_utc(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def day_items(ctx: dict, local_now: datetime) -> tuple[list[str], list[str]]:
    """``(items, time_critical)`` from a gathered day context. Pure.

    Past events are dropped; the portrait and the engineering board are never
    day items. ``time_critical`` holds at most one line: the soonest event
    starting within 2 h, else the first overdue open loop.
    """
    items: list[str] = []
    soonest: tuple[datetime, str] | None = None
    for ev in (ctx or {}).get("calendar") or []:
        title = _clean(ev.get("title"))
        if not title:
            continue
        start, end = _at(local_now, ev.get("start")), _at(local_now, ev.get("end"))
        over = end if end is not None else (start + timedelta(hours=1) if start else None)
        if over is not None and over < local_now:
            continue  # already over
        when = f" at {start.strftime('%H:%M')}" if start else " (all day)"
        where = f", {_clean(ev.get('location'))}" if _clean(ev.get("location")) else ""
        items.append(f"Calendar: {title}{when}{where}")
        if start and local_now <= start <= local_now + _SOON and (soonest is None or start < soonest[0]):
            soonest = (start, f"Soon: {title}{when}")
    overdue = ""
    now_utc = local_now.astimezone(timezone.utc)
    for loop in (ctx or {}).get("open_loops") or []:
        text = _clean(loop.get("hint") or loop.get("text"))
        if not text:
            continue
        items.append(f"To follow up: {text}")
        due = _as_utc(loop.get("due"))
        if not overdue and due is not None and due < now_utc:
            overdue = f"Overdue: {text}"
    for moment in ((ctx or {}).get("emotional_moments") or [])[:1]:
        if _clean(moment):
            items.append(f"Recently on their mind (at most a gentle check-in): {_clean(moment)}")
    critical = [soonest[1]] if soonest else ([overdue] if overdue else [])
    return items, critical


def render_body(shape: str, lines: list[str]) -> str:
    instruction = GREETING_INSTRUCTION if shape == "greeting" else COMMAND_INSTRUCTION
    return instruction + "\n" + "\n".join(f"- {line}" for line in lines)


@dataclass
class DayBrief:
    user_id: str
    shape: str
    items: int
    body: str  # instruction + items, undelimited (the core seam delimits it)
    now: datetime

    @property
    def block(self) -> str:
        """The delimited block the Flue seam appends after the user's words."""
        return f"{BLOCK_LABEL}\n{self.body}\n{BLOCK_CLOSE}"


# ── I/O seams (tests stub these) ─────────────────────────────────────────────
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _claim_taken(user_id: str, now: datetime) -> bool:
    from db_compat import get_compat_db
    from proactive.arrival import brief_claimed

    async with get_compat_db() as db:
        return await brief_claimed(db, user_id=user_id, now=now)


async def _gather(user_id: str, local_date: str) -> dict:
    from db_compat import get_compat_db
    from proactive.triggers.morning_checkin import _build_morning_context

    async with get_compat_db() as db:
        return await _build_morning_context(db, user_id, local_date, include_board=False)


async def _take_claim(user_id: str, now: datetime) -> bool:
    from db_compat import get_compat_db
    from proactive.arrival import claim_full_brief

    async with get_compat_db() as db:
        claim_id = await claim_full_brief(
            db, user_id=user_id, trigger_type=TRIGGER_TYPE, panel_id=None,
            pending_id=None, missed="", now=now,
        )
    return claim_id is not None


def _log(brief_user: str, items: int, shape: str, injected: bool, claimed: bool) -> None:
    logger.info("BRIEF_FIRST_TURN user=%s items=%d shape=%s injected=%d claimed=%d",
                brief_user, items, shape, int(injected), int(claimed))


async def _prepare(message: str, uid: str, now: datetime) -> DayBrief | None:
    local_now = now.astimezone(zoe_timezone())
    if not in_window(local_now):
        return None  # outside the window: nothing, and the claim stays untaken
    try:
        from zoe_flue_client import is_continuity_turn

        if is_continuity_turn(message, uid):
            return None
    except Exception:  # pragma: no cover - in-tree module
        pass
    local_date = local_now.date().isoformat()
    if _settled.get(uid) == local_date:
        return None
    if await _claim_taken(uid, now):
        _settled[uid] = local_date
        return None
    cached = _ctx_cache.get(uid)
    if cached and cached[0] == local_date and time.monotonic() - cached[1] < _GATHER_CACHE_S:
        ctx = cached[2]
    else:
        ctx = await _gather(uid, local_date) or {}
        _ctx_cache[uid] = (local_date, time.monotonic(), ctx)
    items, critical = day_items(ctx, local_now)
    if not items:
        return None  # nothing on: no filler, no claim — a later turn may have something
    shape = turn_shape(message)
    if shape == "command" and not critical:
        _log(uid, len(items), shape, injected=False, claimed=False)
        return None
    lines = items if shape == "greeting" else critical
    return DayBrief(uid, shape, len(items), render_body(shape, lines), now)


async def prepare(message: str, user_id: str) -> DayBrief | None:
    """The day brief for this turn, or None. NEVER raises; time-boxed."""
    uid = (user_id or "").strip()
    if not brief_on_first_turn_enabled() or not uid or is_synthetic_user(uid):
        return None
    try:
        return await asyncio.wait_for(_prepare(message or "", uid, _now_utc()),
                                      timeout=_PREPARE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — the brief must never break a turn
        logger.debug("brief-first-turn: prepare skipped (non-fatal): %r", exc)
        return None


async def settle(brief: DayBrief | None, *, produced: bool) -> bool:
    """Take today's shared claim once the lane PRODUCED a reply. NEVER raises.

    ``produced=False`` (a failed/fallback turn) takes nothing, so the next turn
    still gets the brief. Returns True when this call took the claim.
    """
    if brief is None:
        return False
    claimed = False
    if produced:
        try:
            claimed = await _take_claim(brief.user_id, brief.now)
            _settled[brief.user_id] = brief.now.astimezone(zoe_timezone()).date().isoformat()
        except Exception as exc:  # noqa: BLE001
            logger.warning("brief-first-turn: claim failed for user=%s: %r", brief.user_id, exc)
    _log(brief.user_id, brief.items, brief.shape, injected=True, claimed=claimed)
    return claimed


def _reset_state() -> None:
    """Clear the in-process throttles (tests; simulates a restart)."""
    _settled.clear()
    _ctx_cache.clear()
