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
import uuid
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
# Flue keeps every user message it was sent (nothing elides old blocks there) and a
# session can outlive a household day, so each block names its date.
STALE_INSTRUCTION = "Ignore any earlier [Today …] block with a different date."
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
_FOLLOW, _OVERDUE = "To follow up: ", "Overdue: "
_MOMENT = "Recently on their mind (at most a gentle check-in): "

# In-process throttles only; once-per-day correctness rests on the claim row.
_settled: dict[str, str] = {}  # user -> local date whose claim is known taken
_ctx_cache: dict[str, tuple[str, float, dict]] = {}
_settling: set = set()
# user -> {turn token -> monotonic injection time}: one HOLD per conversational
# brief that is injected and not yet settled. Keyed per TURN, not per user, so
# settling one turn never releases an overlapping one. The daemon claim endpoint
# holds a queued 07:30 brief while ANY of a user's holds is live
# (``scheduled_row_gate``). In-process is enough: zoe-data runs ONE uvicorn
# worker (scripts/setup/systemd/zoe-data.service, no --workers); each hold
# expires after ``_BRIEFING_HOLD_S`` so a lost settle cannot hold the spoken
# brief past its TTL.
_briefing: dict[str, dict[str, float]] = {}
_BRIEFING_HOLD_S = 120.0


def _live_holds(user_id: str) -> dict[str, float]:
    """The user's unexpired holds (expired ones pruned)."""
    holds = _briefing.get(user_id) or {}
    now = time.monotonic()
    for token in [t for t, at in holds.items() if now - at >= _BRIEFING_HOLD_S]:
        holds.pop(token, None)
    if not holds:
        _briefing.pop(user_id, None)
    return holds


def _release_hold(user_id: str, token: str) -> None:
    holds = _briefing.get(user_id)
    if holds is not None:
        holds.pop(token, None)
        if not holds:
            _briefing.pop(user_id, None)


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


def _stored_end(ev: dict, start: datetime | None, local_now: datetime) -> datetime:
    """When an event ends per what the calendar STORED: ``end_time`` (unless
    ``end_date`` is a later day), else ``start + duration`` minutes (the panel's
    default entry mode stores only a duration), else never today — the voice
    writer (``intent_router`` → ``create_event_record``) stores neither, so a
    missing end is no proof the event is over."""
    never = local_now + timedelta(days=1)
    end_date = str(ev.get("end_date") or "")[:10]
    if end_date and end_date > local_now.date().isoformat():
        return never
    end = _at(local_now, ev.get("end"))
    if end is not None:
        return end
    try:
        minutes = int(ev.get("duration") or 0)
    except (TypeError, ValueError):
        minutes = 0
    return start + timedelta(minutes=minutes) if start and minutes > 0 else never


def day_items(ctx: dict, local_now: datetime) -> tuple[list[str], list[str]]:
    """``(items, time_critical)`` from a gathered day context. Pure.

    Events whose stored end has passed are dropped; the portrait and the engineering board are never
    day items. ``time_critical`` holds at most one line: the soonest event
    starting within 2 h, else the first overdue open loop.
    """
    items: list[str] = []
    soonest: tuple[datetime, str] | None = None
    for ev in (ctx or {}).get("calendar") or []:
        title = _clean(ev.get("title"))
        if not title:
            continue
        start = _at(local_now, ev.get("start"))
        if _stored_end(ev, start, local_now) < local_now:
            continue  # its STORED end has passed; no stored end = still on
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
        items.append(_FOLLOW + text)
        due = _as_utc(loop.get("due"))
        if not overdue and due is not None and due < now_utc:
            overdue = _OVERDUE + text
    for moment in ((ctx or {}).get("emotional_moments") or [])[:1]:
        if _clean(moment):
            items.append(_MOMENT + _clean(moment))
    critical = [soonest[1]] if soonest else ([overdue] if overdue else [])
    return items, critical


def mentioned(ctx: dict, lines: list[str]) -> list[tuple[str, str, str]]:
    """``(kind, source_ref, text)`` of the loops and the moment whose line IS in the
    rendered brief — bounded to what was said, keyed like the selector's candidates
    (``open_loops:<id>`` / ``memory:<id>``). Pure."""
    said, out = set(lines), []
    for loop in (ctx or {}).get("open_loops") or []:
        text = _clean(loop.get("hint") or loop.get("text"))
        if loop.get("id") is not None and text and {_FOLLOW + text, _OVERDUE + text} & said:
            out.append(("open_loop", f"open_loops:{loop['id']}", str(loop.get("text") or "")))
    moments = (ctx or {}).get("emotional_moments") or []
    ids = (ctx or {}).get("emotional_moment_ids") or []
    if moments and ids and _MOMENT + _clean(moments[0]) in said:
        out.append(("emotional", f"memory:{ids[0]}", str(moments[0])))
    return out


def render_body(shape: str, lines: list[str], local_date: str) -> str:
    instruction = GREETING_INSTRUCTION if shape == "greeting" else COMMAND_INSTRUCTION
    head = f"Today is {local_date}. {instruction} {STALE_INSTRUCTION}"
    return head + "\n" + "\n".join(f"- {line}" for line in lines)


def trigger_key(message: str | None) -> str:
    """A short digest of the turn's normalised words: lets the delivery ledger tell a later
    copy of the triggering utterance from the member's real next turn (no text is kept)."""
    import hashlib

    return hashlib.sha1(_normalize(message).encode("utf-8")).hexdigest()[:16]


@dataclass
class DayBrief:
    user_id: str
    shape: str
    items: int
    body: str  # instruction + items, undelimited (the core seam delimits it)
    now: datetime
    local_date: str
    token: str = ""  # this turn's hold on a queued 07:30 brief (``_briefing``)
    session_id: str = ""
    surfaced: tuple = ()  # ``mentioned`` items, marked at settle (ZOE_LOOP_LIFECYCLE)
    trigger: str = ""  # ``trigger_key`` of the utterance this brief rode in on

    @property
    def block(self) -> str:
        """The block the Flue seam appends after the user's words, its label DATED
        (``[Today 2026-09-29]``) because Flue never elides a superseded copy. The
        core seam uses the bare ``BLOCK_LABEL`` — its strip matches whole lines
        and removes every older copy anyway."""
        return f"{BLOCK_LABEL[:-1]} {self.local_date}]\n{self.body}\n{BLOCK_CLOSE}"


# ── I/O seams (tests stub these) ─────────────────────────────────────────────
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _claim_state(user_id: str, now: datetime) -> str:
    """``taken`` / ``in_flight`` / ``free`` for today's shared claim.

    ``taken`` when the claim row exists, or when today's 07:30 full brief was
    PLAYED (the daemon's ACK, never its claim) — the scheduled path queues
    without a claim while only this flag is on, so its success is claimed here,
    the first time it is seen. ``in_flight``: that brief is queued or claimed and
    not yet acknowledged, within its bounded window — wait, do not talk over it.
    Anything else (expired, never played, only the guest teaser) is ``free``.
    """
    from db_compat import get_compat_db
    from proactive import arrival

    async with get_compat_db() as db:
        if await arrival.brief_claimed(db, user_id=user_id, now=now):
            return "taken"
        delivery = await arrival.scheduled_brief_delivery(db, user_id=user_id, now=now)
        if delivery == "played":
            await arrival.claim_full_brief(
                db, user_id=user_id, trigger_type=arrival.BRIEF_TRIGGER, panel_id=None,
                pending_id=None, missed="", now=now,
            )
            return "taken"
    return "in_flight" if delivery == "in_flight" else "free"


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


async def _prepare(message: str, uid: str, now: datetime, sid: str = "") -> DayBrief | None:
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
    state = await _claim_state(uid, now)
    if state == "taken":
        _settled[uid] = local_date
    if state != "free":
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
    from open_loop_lifecycle import lifecycle_enabled

    surfaced = tuple(mentioned(ctx, lines)) if sid and lifecycle_enabled() else ()
    token = uuid.uuid4().hex
    _briefing.setdefault(uid, {})[token] = time.monotonic()
    return DayBrief(uid, shape, len(items), render_body(shape, lines, local_date), now,
                    local_date, token, sid, surfaced, trigger_key(message))


async def prepare(message: str, user_id: str, session_id: str = "") -> DayBrief | None:
    """The day brief for this turn, or None. NEVER raises; time-boxed. ``session_id``
    lets settle mark what the brief mentioned as surfaced for the selector."""
    uid = (user_id or "").strip()
    if not brief_on_first_turn_enabled() or not uid or is_synthetic_user(uid):
        return None
    try:
        return await asyncio.wait_for(
            _prepare(message or "", uid, _now_utc(), (session_id or "").strip()),
            timeout=_PREPARE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — the brief must never break a turn
        logger.debug("brief-first-turn: prepare skipped (non-fatal): %r", exc)
        return None


async def settle(brief: DayBrief | None, *, produced: bool) -> bool:
    """Take today's shared claim once the turn EMITTED reply text. NEVER raises.

    Both lanes call this from their stream's ``finally``, so a turn that ended in
    a disconnect, a barge-in or an error AFTER its first text still takes the
    claim (the brief was said, or started — repeating it is the worse failure).
    ``produced=False`` (no text at all: an error before the first token, the
    canned fallback) takes nothing, so the next turn still gets the brief.

    Shielded: the claim write finishes even when the turn's task is being
    cancelled (a barge-in cancels it mid-stream). Returns True when this call
    took the claim.
    """
    if brief is None:
        return False
    task = asyncio.ensure_future(_settle(brief, produced))
    _settling.add(task)  # a shielded task must stay referenced until it finishes
    task.add_done_callback(_settling.discard)
    return await asyncio.shield(task)


async def _settle(brief: DayBrief, produced: bool) -> bool:
    # ORDER matters: the claim is written BEFORE this turn's hold is released, so
    # a daemon poll can never find neither (hold gone, claim not yet written) and
    # play a queued 07:30 brief the member just heard. A failed claim write keeps
    # the hold until its own expiry — the brief went out, so the spoken copy stays
    # held for as long as the hold is allowed to last.
    claimed = False
    keep_hold = False
    if produced:
        try:
            claimed = await _take_claim(brief.user_id, brief.now)
            _settled[brief.user_id] = brief.now.astimezone(zoe_timezone()).date().isoformat()
        except Exception as exc:  # noqa: BLE001
            keep_hold = True
            logger.warning("brief-first-turn: claim failed for user=%s: %r", brief.user_id, exc)
    if not keep_hold:
        _release_hold(brief.user_id, brief.token)
    if produced and brief.surfaced:
        # The loops it voiced are surfaced: the next conversation must not raise them.
        from proactive.selector import mark_brief_surfaced

        await mark_brief_surfaced(brief.user_id, brief.session_id, list(brief.surfaced),
                                  **({"trigger": brief.trigger} if brief.trigger else {}))
    _log(brief.user_id, brief.items, brief.shape, injected=True, claimed=claimed)
    return claimed


async def scheduled_row_gate(db, user_id: str) -> str:
    """For the daemon claim of a queued 07:30 brief row: ``play`` / ``defer`` /
    ``suppress``. Never raises (an error plays, the pre-existing behaviour).

    Closes the read-then-queue race: the 07:30 path reads a free claim, a first
    turn injects the brief meanwhile, the 07:30 row is queued anyway. While that
    conversational brief is mid-reply the row is held (``defer``: left pending,
    its TTL counting); once the first-turn brief holds the claim the row is
    never played (``suppress``). With this flag off: ``play``, no I/O.
    """
    if not brief_on_first_turn_enabled() or not user_id:
        return "play"
    if _live_holds(user_id):
        return "defer"
    try:
        from proactive.arrival import claim_holder

        holder = await claim_holder(db, user_id=user_id, now=_now_utc())
    except Exception as exc:  # noqa: BLE001
        logger.debug("brief-first-turn: gate read failed, playing: %r", exc)
        return "play"
    return "suppress" if holder == TRIGGER_TYPE else "play"


def _reset_state() -> None:
    """Clear the in-process throttles (tests; simulates a restart)."""
    _settled.clear()
    _ctx_cache.clear()
    _briefing.clear()
