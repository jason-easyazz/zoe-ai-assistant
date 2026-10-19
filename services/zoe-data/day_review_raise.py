"""The day review, as a conversation (memories-space P6): the raise, the pull, the orb and the crowded-room phone send.

Flag-dark, TWO keys: ``ZOE_DAY_REVIEW_RAISE`` (default OFF, read per call) AND ``ZOE_OMI_DAY_REVIEW=on`` (``shadow`` is "no
mention, no raise, no voice" by the day review's own contract). Either off = every entry point returns before any I/O and the
callers behave byte for byte as before. Design: ``docs/architecture/memories-space.md`` section 5.1; record
``docs/knowledge/day-review-raise.md``.

NEVER UNPROMPTED (owner W2: ``ZOE_PROACTIVE_SPOKEN=0``). Zoe never speaks first and never asks:

* **The raise** rides the first-turn brief (``brief_first_turn``, ``ZOE_BRIEF_ON_FIRST_TURN``): on the member's first
  greeting / open turn of the morning, ONE code-composed line joins the ``[Today]`` block ("I went through your day - there
  are four things for you to look at whenever you like (...)"). Counts by kind only; no names, no quotes, no question. A
  command turn never carries it. At most ONCE a household day; after ``MAX_UNANSWERED`` (2) raises in a row that the member
  did not take up (no pull, no card, no decision on any item), or one decline ("not now", "leave it" right after it), it is
  SNOOZED: raised again only when a NEWER review is waiting AND ``ZOE_DAY_REVIEW_RAISE_SNOOZE_DAYS`` (3) have passed.
* **The pull**: "what did you find in my day?", "anything from today?" -> a counts-only spoken summary, and the private
  "Your day" card (``panel_review_cards``, ``ZOE_PANEL_REVIEW_CARDS``) on THIS panel. "What's up?" (``proactive.pull``) adds
  the same one sentence when something is waiting.
* **The orb**: ``GET /api/proactive/inbox`` gains ``review: true`` (no count, no content), painted as a quiet ring. No sound.

WHO MAY HEAR IT. Only the member whose candidates they are, and only on a turn that IDENTIFIED them: an open voice session or a
fresh PIN on that panel (the voice lane's ``scope_user``, noted per session by ``note_voice_turn`` - the streaming brain runs
outside the request task, so no ContextVar), or a typed chat in their own signed-in session. A guest, an unverified voice and
another member get nothing - not even the count. A voice turn with no note (an unknown lane) is refused, never assumed.

CROWDED ROOM (identity-presence-engine section 2.1: another voice heard on this panel in the last 10 minutes). The raise is
not made (it waits for another day). A pull says nothing private aloud: the count goes to the member's linked Telegram
(``telegram_notify``: the count + the Pendant page link, no item text) and the panel gets the locked card. Honest limits: only
spoken turns count (an unverified voice, a turn with NO verdict, or another member's voice) - a silent person is invisible;
for the first 10 minutes after a restart nothing is known, so the room counts as crowded.

READ-ONLY on the household's data. Raising and pulling never write a candidate, a list, a reminder, an event or a memory: the
day review's approve-before-write stays the only writer (``omi_review_apply``). This module keeps only its own small state
(the ``day_review_raise`` / ``day_review_engaged_at`` keys of ``user_prefs``: dates and a counter, no text). No item text is
ever logged.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_DAY_REVIEW_RAISE"
TIER = "day_review"
STATE_KEY = "day_review_raise"
ENGAGED_KEY = "day_review_engaged_at"
MAX_UNANSWERED = 2           # raises in a row nobody took up before the raise snoozes
CROWD_WINDOW_S = 600.0       # "another voice heard on this panel in the last 10 minutes"
TURN_TTL_S = 180.0           # a voice turn's note covers the brain call that follows it
DECLINE_WINDOW_S = 600.0     # "not now" this soon after a raise is about the raise
PHONE_GAP_S = 600.0          # at most one phone send per member per 10 minutes
_PANEL_LOG_MAX = 32

PRIVATE_REPLY = "That's private. Say it again so I know it's you, or unlock with your PIN."
NOTHING_REPLY = "Nothing from your day is waiting."
CROWDED_SENT = "That's private and someone else is here, so I've sent it to your phone."
CROWDED_TRIED = "That's private and someone else is here. I've tried sending it to your phone."
CROWDED_NO_PHONE = "That's private, and someone else is here. Ask me again when you're alone."
PHONE_SENT = "I've sent it to your phone."
PHONE_NONE = "I can't reach your phone - link Telegram in Settings first."
_TS = "%Y-%m-%dT%H:%M:%SZ"


def enabled() -> bool:
    """Both keys: ``ZOE_DAY_REVIEW_RAISE`` and the day review itself ``on`` (shadow never talks)."""
    from typed_env import env_bool

    if not env_bool("ZOE_DAY_REVIEW_RAISE", False):
        return False
    try:
        import omi_day_review

        return omi_day_review.mode() == "on"
    except Exception:  # noqa: BLE001 - no day review, nothing to raise
        return False


def snooze_days() -> int:
    from typed_env import env_int

    return max(1, min(30, env_int("ZOE_DAY_REVIEW_RAISE_SNOOZE_DAYS", 3)))


# -- the words (code-composed from counts; never a name, a quote or a time) --------------------------------------------------
_KINDS = (("calendar_event", "event", "events"), ("list_item", "thing for your lists", "things for your lists"),
          ("reminder", "reminder", "reminders"), ("follow_up", "thing to do", "things to do"),
          ("person", "person to remember", "people to remember"), ("owner_fact", "thing about you", "things about you"))
_NUM = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def _n(n: int) -> str:
    return _NUM.get(n, str(n))


def _listed(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _count(v: Any) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def breakdown(counts: dict) -> str:
    """"two events, three things for your lists and one reminder". Pure. Unknown kinds are "other things"."""
    parts, known = [], {k for k, _s, _p in _KINDS}
    for kind, one, many in _KINDS:
        n = _count((counts or {}).get(kind))
        if n > 0:
            parts.append(f"{_n(n)} {one if n == 1 else many}")
    other = sum(_count(v) for k, v in (counts or {}).items() if k not in known)
    if other > 0:
        parts.append(f"{_n(other)} other thing{'s' if other != 1 else ''}")
    return _listed(parts) if parts else ""


def total(counts: dict) -> int:
    return sum(_count(v) for v in (counts or {}).values())


def raise_line(counts: dict) -> str:
    """The brief's one line. Pure."""
    n = total(counts)
    return (f"I went through your day - there {'is' if n == 1 else 'are'} {_n(n)} thing{'s' if n != 1 else ''} for you to look "
            f"at whenever you like ({breakdown(counts)}).")


def summary(counts: dict) -> str:
    """The pull's spoken summary. Pure."""
    return f"I went through your day and found {breakdown(counts)}."


def phone_text(counts: dict) -> str:
    """What goes to Telegram (off the box): the count only - no kinds, no words from the day."""
    n = total(counts)
    return f"I went through your day: {_n(n)} thing{'s' if n != 1 else ''} {'is' if n == 1 else 'are'} waiting for you to look at."


# -- the phrases (whole-utterance, anchored; the panel_review_cards grammar) ---------------------------------------------------
_LEAD = r"^\W*(?:(?:so|and|hey|ok|okay|um|uh|zoe|please|right|oh|well)[\s,]+)*"
_TAIL = r"(?:[\s,]+(?:again|please|then|now|zoe|thanks|today))*\W*$"


def _rx(body: str) -> re.Pattern:
    return re.compile(_LEAD + body + _TAIL, re.IGNORECASE)


_PULL = (
    _rx(r"what\s+did\s+you\s+(?:find|get|pick\s+up|spot)\s+(?:in|from)\s+(?:my\s+day|my\s+pendant|today)"),
    _rx(r"what\s+did\s+you\s+find\s+today"),
    _rx(r"(?:is\s+there\s+|was\s+there\s+|got\s+|have\s+you\s+got\s+)?anything\s+(?:interesting\s+)?from\s+(?:my\s+day|today|my\s+pendant)"),
    _rx(r"(?:what(?:['’]s|\s+is)|anything)\s+(?:new\s+)?(?:in|from)\s+my\s+day\s+review"),
    _rx(r"how\s+did\s+my\s+day\s+review\s+go"),
)
_PHONE = (
    _rx(r"send\s+(?:my\s+day(?:\s+review)?|the\s+day\s+review|what\s+you\s+found(?:\s+in\s+my\s+day)?)\s+to\s+my\s+phone"),
)
_DECLINE = _rx(r"(?:not\s+now|not\s+today|not\s+right\s+now|later|maybe\s+later|no\s+thanks|no\s+thank\s+you|leave\s+it|"
               r"not\s+interested|i['’]?ll\s+(?:look|do\s+it|check)\s+later|"
               r"(?:don['’]?t|do\s+not)\s+(?:mention|bring\s+up)\s+(?:that|it|my\s+day)(?:\s+again)?|"
               r"stop\s+(?:mentioning|telling\s+me\s+about)\s+(?:that|it|my\s+day))")


def classify(text: str | None) -> Optional[str]:
    """``pull`` | ``phone`` | None. Pure; nothing longer than 140 characters is ours."""
    t = (text or "").strip()
    if not t or len(t) > 140:
        return None
    if any(rx.match(t) for rx in _PULL):
        return "pull"
    if any(rx.match(t) for rx in _PHONE):
        return "phone"
    return None


def is_decline(text: str | None) -> bool:
    t = (text or "").strip()
    return bool(t) and len(t) <= 80 and bool(_DECLINE.match(t))


# -- who is speaking, and who else the panel heard (in-process; zoe-data is ONE uvicorn worker) ----------------------------------
@dataclass
class _VoiceTurn:
    panel_id: str
    scope_user: Optional[str]
    at: float


_TURNS: dict[str, _VoiceTurn] = {}
_HEARD: dict[str, list[tuple[float, str]]] = {}    # panel -> [(monotonic, member id | "?")]
_STARTED: list[float] = [time.monotonic()]          # when this process started: who is in the room is unknown for 10 min
_PHONE_AT: dict[str, float] = {}


def _clock() -> float:
    return time.monotonic()


def note_voice_turn(session_id: str, panel_id: str, *, scope_user: Optional[str], verdict: Optional[bool],
                    claimed: Optional[str]) -> None:
    """Called by the voice lane once per spoken turn, after it resolved the turn's identity. Off = nothing. NEVER raises.

    ``scope_user`` is the member this turn may act for (an accepted voice claim / an open voice session / a FRESH PIN; None =
    nobody). ``verdict`` + ``claimed`` feed the crowd log: a voice the gate judged and did NOT confirm is "someone", another
    member's confirmed voice is that member, and a turn with NO verdict is an unknown voice too (unknown is never the member)."""
    try:
        if not enabled():
            return
        now = _clock()
        if session_id:
            _TURNS[str(session_id)] = _VoiceTurn(str(panel_id or ""), (scope_user or None), now)
            for sid in [s for s, t in _TURNS.items() if now - t.at > TURN_TTL_S]:
                _TURNS.pop(sid, None)
        who = str(claimed) if (verdict is True and claimed) else "?"
        if panel_id and who:
            log = [e for e in _HEARD.get(str(panel_id), []) if now - e[0] <= CROWD_WINDOW_S]
            log.append((now, who))
            _HEARD[str(panel_id)] = log[-_PANEL_LOG_MAX:]
    except Exception as exc:  # noqa: BLE001
        logger.debug("day_review_raise: voice note skipped: %s", type(exc).__name__)


def others_present(panel_id: str, member: str) -> bool:
    """True when anyone but ``member`` was heard on ``panel_id`` in the last 10 minutes - or when that cannot be known yet
    (within this process's first 10 minutes): unknown is crowded."""
    now = _clock()
    if not _STARTED or now - _STARTED[0] < CROWD_WINDOW_S:
        return True
    return any(now - t <= CROWD_WINDOW_S and who != member for t, who in _HEARD.get(str(panel_id or ""), []))


def _account(uid: str) -> bool:
    try:
        import identity_facts
        from user_filters import is_synthetic_user, is_unidentified

        uid = (uid or "").strip()
        return (bool(uid) and not uid.startswith("panel") and not is_unidentified(uid) and not is_synthetic_user(uid)
                and identity_facts._is_account_id(uid))
    except Exception:  # noqa: BLE001 - unknown is nobody
        return False


def turn_gate(user_id: str, session_id: str) -> tuple[bool, str]:
    """May THIS brain turn mention the member's day review? ``(ok, reason)``. Pure but for the in-process notes."""
    uid = (user_id or "").strip()
    if not _account(uid):
        return False, "not_member"
    sid = str(session_id or "")
    turn = _TURNS.get(sid)
    if turn is not None and _clock() - turn.at <= TURN_TTL_S:
        if turn.scope_user != uid:
            return False, "unidentified"
        if not turn.panel_id or others_present(turn.panel_id, uid):
            return False, "crowded"
        return True, "voice"
    if sid.startswith("voice"):
        return False, "voice_unnoted"      # a spoken turn whose identity was never noted: refused, never assumed
    return True, "chat"


# -- state (user_prefs: dates and a counter, no text) ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS)


def _local_day(now: datetime) -> str:
    from time_utils import zoe_timezone

    return now.astimezone(zoe_timezone()).date().isoformat()


def _day_of(stamp: str) -> str:
    """The household day of a stored UTC stamp; "" when unreadable."""
    try:
        return _local_day(datetime.strptime(stamp, _TS).replace(tzinfo=timezone.utc))
    except ValueError:
        return ""


async def _load_state(uid: str) -> tuple[dict, str]:
    """``(raise state, engaged_at)``; ``({}, "")`` when there is none. RAISES on a read error (the caller fails closed)."""
    from db_pool import get_db_ctx
    from user_prefs import read_prefs

    async with get_db_ctx() as db:
        prefs = await read_prefs(db, uid)
    st = prefs.get(STATE_KEY)
    return (st if isinstance(st, dict) else {}), str(prefs.get(ENGAGED_KEY) or "")


async def _save(uid: str, key: str, value: Any) -> None:
    from user_prefs import set_pref

    await set_pref(uid, key, value)


async def _facts(uid: str, since: str) -> dict:
    import omi_day_review

    return await omi_day_review.raise_facts(uid, since)


async def _counts(uid: str) -> dict:
    import omi_day_review

    return await omi_day_review.pending_counts(uid)


def _streak(state: dict, engaged_at: str, facts: dict) -> int:
    """Raises in a row the member did not take up. Taking it up = a pull or the card (``engaged_at``) or a decision on any
    item (``decided_since``) after the last raise. Pure."""
    last = str(state.get("at") or "")
    if not last:
        return 0
    if (engaged_at and engaged_at >= last) or facts.get("decided_since"):
        return 0
    try:
        return max(0, int(state.get("unanswered") or 0))
    except (TypeError, ValueError):
        return MAX_UNANSWERED


def _snoozed(state: dict, streak: int, facts: dict, now: datetime) -> bool:
    """After ``MAX_UNANSWERED`` ignored raises (or a decline): quiet until a NEWER review is waiting AND the snooze days have
    passed since the last raise. The same old items are never raised again. Pure."""
    if streak < MAX_UNANSWERED:
        return False
    last = str(state.get("at") or "")
    newer = bool(last) and str(facts.get("newest") or "") > last
    try:
        waited = now - datetime.strptime(last, _TS).replace(tzinfo=timezone.utc) >= timedelta(days=snooze_days())
    except ValueError:
        waited = True
    return not (newer and waited)


@dataclass
class Raise:
    user_id: str
    line: str
    streak: int
    now: datetime


async def prepare(user_id: str, session_id: str, *, now: Optional[datetime] = None) -> Optional[Raise]:
    """The raise for this brief, or None. Called from ``brief_first_turn._prepare`` (inside its time box) only on a greeting
    turn. NEVER raises; any error is no raise (fail closed)."""
    if not enabled():
        return None
    uid = (user_id or "").strip()
    try:
        ok, reason = turn_gate(uid, session_id)
        if not ok:
            logger.info("DAY_REVIEW_RAISE user=%s raised=0 reason=%s", uid or "-", reason)
            return None
        now = now or _now()
        state, engaged_at = await _load_state(uid)
        if state.get("day") == _local_day(now):
            return None                                           # at most once a household day
        if engaged_at and _day_of(engaged_at) == _local_day(now):
            return None                                           # he already asked or looked today: nothing to mention
        facts = await _facts(uid, str(state.get("at") or ""))
        counts = facts.get("counts") or {}
        if total(counts) <= 0:
            return None
        streak = _streak(state, engaged_at, facts)
        if _snoozed(state, streak, facts, now):
            logger.info("DAY_REVIEW_RAISE user=%s raised=0 reason=snoozed streak=%d", uid, streak)
            return None
        if streak >= MAX_UNANSWERED:
            streak = 0                                            # a newer review after the snooze: a fresh start
        return Raise(uid, raise_line(counts), streak, now)
    except Exception as exc:  # noqa: BLE001
        logger.info("DAY_REVIEW_RAISE user=%s raised=0 reason=error:%s", uid or "-", type(exc).__name__)
        return None


async def settle(raised: Optional[Raise]) -> None:
    """The brief that carried the line produced a reply: today is used, and the raise counts as unanswered until the member
    takes it up. NEVER raises."""
    if raised is None or not enabled():
        return                                                    # switched off mid-turn: no write (the rollback is instant)
    try:
        await _save(raised.user_id, STATE_KEY, {"day": _local_day(raised.now), "at": _iso(raised.now),
                                                "unanswered": raised.streak + 1})
        logger.info("DAY_REVIEW_RAISE user=%s raised=1 unanswered=%d", raised.user_id, raised.streak + 1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("day_review_raise: raise not recorded for user=%s: %s", raised.user_id, type(exc).__name__)


async def note_engaged(member: str, *, dry: bool = False) -> None:
    """The member took the review up (a pull, the card). NEVER raises."""
    if dry or not member or not enabled():
        return
    try:
        await _save(member, ENGAGED_KEY, _iso(_now()))
    except Exception as exc:  # noqa: BLE001
        logger.debug("day_review_raise: engaged not recorded: %s", type(exc).__name__)


async def note_declined(member: str) -> bool:
    """"Not now" within ``DECLINE_WINDOW_S`` of a raise: snooze it now (the state two ignores reach). The STORED raise time is
    the authority, so a restart in between does not lose the decline. True when it was about a raise."""
    if not enabled():
        return False
    try:
        state, _engaged = await _load_state(member)
        try:
            raised_at = datetime.strptime(str(state.get("at") or ""), _TS).replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        if not timedelta(0) <= _now() - raised_at <= timedelta(seconds=DECLINE_WINDOW_S):
            return False
        state = dict(state)
        state["unanswered"] = MAX_UNANSWERED
        await _save(member, STATE_KEY, state)
        logger.info("DAY_REVIEW_RAISE user=%s declined=1", member)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("day_review_raise: decline not recorded: %s", type(exc).__name__)
        return False


# -- the orb ----------------------------------------------------------------------------------------------------------------------
async def orb_review(user_id: str) -> Optional[bool]:
    """``True`` / ``False`` for the inbox's ``review`` field; None = the feature is off (the field is left out, so the response
    is unchanged). A count is never returned."""
    if not enabled():
        return None
    uid = (user_id or "").strip()
    if not _account(uid):
        return False
    try:
        return total(await _counts(uid)) > 0
    except Exception:  # noqa: BLE001
        return False


# -- the pull -----------------------------------------------------------------------------------------------------------------
def _scope_member(user_id: str, channel: str, extra_ctx: Optional[dict]) -> Optional[str]:
    """The turn's identified member (panel_review_cards' rule: voice = ``scope_user``, chat = the signed-in caller)."""
    try:
        import panel_review_cards

        m = panel_review_cards._member(user_id, channel, extra_ctx)
    except Exception:  # noqa: BLE001
        return None
    return m if m and _account(m) else None


async def _telegram_id(member: str) -> Optional[str]:
    from user_prefs import get_pref

    tid = await get_pref(member, "telegram_id")
    return tid if isinstance(tid, str) and tid.isdigit() else None


def _phone_url() -> Optional[str]:
    from typed_env import env_str

    base = env_str("ZOE_PUBLIC_URL").rstrip("/")
    return f"{base}/pendant.html" if base else None


async def send_to_phone(member: str, counts: dict) -> str:
    """``sent`` | ``uncertain`` | ``none`` (no linked phone, the bot not configured, refused, or too soon after the last).
    The count only; the link opens the member's own Pendant page, which asks for their sign-in."""
    now = _clock()
    if now - _PHONE_AT.get(member, -1e9) < PHONE_GAP_S:
        return "none"
    try:
        import telegram_notify

        tid = await _telegram_id(member)
        if not tid or not telegram_notify.available():
            return "none"
        url = _phone_url()
        outcome = await telegram_notify.send(tid, phone_text(counts), url=url, label="Review your day" if url else None)
    except Exception as exc:  # noqa: BLE001
        logger.info("day_review_raise: phone send failed: %s", type(exc).__name__)
        return "none"
    if outcome in ("sent", "uncertain"):
        _PHONE_AT[member] = now
    logger.info("DAY_REVIEW_PHONE user=%s outcome=%s", member, outcome)
    return outcome if outcome in ("sent", "uncertain") else "none"


async def _push_card(panel_id: str, member: str) -> bool:
    """The private "Your day" card on THIS panel (``panel_review_cards``), when that feature is on."""
    try:
        import panel_review_cards as prc

        if not prc.enabled():
            return False
        cands, voices, people = await prc.day_parts(member)
        return bool(await prc._push(prc.day_card(panel_id, member, cands, voices, people)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("day_review_raise: card not pushed: %s", type(exc).__name__)
        return False


async def _push_locked(panel_id: str) -> None:
    try:
        import voice_cards

        await voice_cards.push(voice_cards.locked(panel_id))
    except Exception:  # noqa: BLE001
        pass


async def answer(text: str, user_id: str, *, channel: str, extra_ctx: Optional[dict], dry: bool = False) -> Optional[str]:
    """The reply for a pull / phone phrase, else None. READ-ONLY on the household's data."""
    kind = classify(text)
    if kind is None:
        return None
    ctx = extra_ctx or {}
    panel_id = str(ctx.get("panel_id") or "") if channel == "voice" else ""
    member = _scope_member(user_id, channel, ctx)
    if member is None:
        if panel_id and not dry:
            await _push_locked(panel_id)
        return PRIVATE_REPLY
    counts = await _counts(member)
    if total(counts) <= 0:
        await note_engaged(member, dry=dry)
        return NOTHING_REPLY
    crowded = bool(panel_id) and others_present(panel_id, member)
    if kind == "phone" or crowded:
        if crowded and not dry:
            await _push_locked(panel_id)                         # the list leaves the wall
        outcome = "none" if dry else await send_to_phone(member, counts)
        if outcome != "none":
            await note_engaged(member, dry=dry)
        if not crowded:
            return PHONE_SENT if outcome != "none" else PHONE_NONE
        return {"sent": CROWDED_SENT, "uncertain": CROWDED_TRIED}.get(outcome, CROWDED_NO_PHONE)
    on_screen = bool(panel_id) and not dry and await _push_card(panel_id, member)
    await note_engaged(member, dry=dry)
    where = (" They're on the screen." if on_screen else
             " You can go through them on the Pendant page." if channel == "chat" else
             " You can go through them on the Pendant page on your phone.")
    return summary(counts) + where


async def pull_addendum(user_id: str, channel: str, extra_ctx: Optional[dict], *, dry: bool = False) -> str:
    """One sentence a "what's up?" pull appends when the member's day review is waiting: counts only, never in a crowded room,
    never to an unidentified voice. "" otherwise. NEVER raises."""
    try:
        if not enabled():
            return ""
        ctx = extra_ctx or {}
        member = _scope_member(user_id, channel, ctx)
        panel_id = str(ctx.get("panel_id") or "") if channel == "voice" else ""
        if member is None or (panel_id and others_present(panel_id, member)):
            return ""
        counts = await _counts(member)
        if total(counts) <= 0:
            return ""
        await note_engaged(member, dry=dry)
        return f"Also, {summary(counts)}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("day_review_raise: pull addendum skipped: %s", type(exc).__name__)
        return ""


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
               extra_ctx: Optional[dict[str, Any]] = None, allow_writes: bool = True):
    """The fast-tier hook (voice + chat): a ``DispatchResult`` for a pull / phone phrase, else None. A decline right after a
    raise is RECORDED and still returns None (the pull's own "not now" handling and the brain answer it as before). Off =
    None before any work. NEVER raises."""
    try:
        ch = (channel or "").strip().lower()
        if ch not in ("voice", "chat") or not enabled():
            return None
        if is_decline(text):
            if allow_writes:
                member = _scope_member(user_id, ch, extra_ctx)
                if member:
                    await note_declined(member)
            return None
        reply = await answer(text, user_id, channel=ch, extra_ctx=extra_ctx, dry=not allow_writes)
        if not reply:
            return None
        import expert_dispatch as _xd

        logger.info("DAY_REVIEW_PULL answered=1 channel=%s private=%d", ch, int(reply == PRIVATE_REPLY))
        return _xd.DispatchResult(domain="memory", reply=reply, intent="day_review", tier=TIER)
    except Exception as exc:  # noqa: BLE001
        logger.warning("day_review_raise: tier failed (non-fatal): %s", type(exc).__name__)
        return None


def reset() -> None:
    """Tests: forget the in-process notes."""
    _TURNS.clear()
    _HEARD.clear()
    _STARTED[:] = [_clock()]
    _PHONE_AT.clear()
