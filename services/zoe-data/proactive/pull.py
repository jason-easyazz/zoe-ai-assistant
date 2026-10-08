"""Pull, not push (register BH1) — ``ZOE_PULL_NOT_PUSH`` (default ON; ``off`` is the kill switch).

The owner turned the unprompted spoken brief OFF (2026-09-29) and the field agrees (OpenAI retired
Pulse for user-scheduled tasks; Alexa's ring and Google's light say "I have something" and speak
only when asked). Zoe already decides, every night, what is worth raising
(``proactive_candidates``, ``proactive/selector.py``). This module is the PULL side of that queue -
it generates NOTHING new:

  * **The pending queue** = the selector's own candidates that it would still raise: not expired,
    out of cooldown, raised fewer than ``MAX_SURFACED`` times. ``pending_state`` reduces it to
    ``{count, top, quiet}`` - a count and the coarse class of the top item (``question`` |
    ``notify``), NEVER content - for the panel's orb to show a quiet "there's something" state
    (no toast, no speech). A guest, or a member who has nothing pending, gets count 0.
  * **The pull** - "what's up?" / "anything for me?" / "what have I got?" (+ variants) is a
    deterministic tier before the router (``fast_tiers._pull_tier``). It delivers the pending items
    ONCE, highest salience first (the selector's own priority), in Zoe's voice, marks each
    delivered the way a raise is (count, cooldown, session: the selector and the brief never
    re-raise it), and clears the queue. Spoken: at most three, the rest stay pending for the next
    ask; chat: all of them, with a one-tap "was that welcome?" row. Empty: "Nothing new for you
    right now." - unless the morning brief is about to deliver (then the brain answers, as before).
    A pull ignores the raise gap, the daily cap and the class back-off: the person asked.
  * **The welcome tap** - "that was welcome" / "that was fine" / "not now", right after a raise or a
    pull, is recorded on the delivery ledger (``proactive/lines.py``) and, with
    ``ZOE_DELIVERY_LEDGER=on``, tunes RAISING for that item class only. Never tone.

Privacy: only a real member's own queue is read; a guest never reaches it. A candidate carrying a
restraint-tier ``sensitivity`` class (the ``feat/restraint-in-code`` work; absent today, read by
name when present) is held back on the spoken lane when the speaker is not verified and offered on
chat instead. No item text is ever logged.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from proactive import lines

logger = logging.getLogger(__name__)

_OFF = {"0", "false", "no", "off"}
_SPOKEN_CHANNELS = frozenset({"voice", "livekit"})
SPOKEN_CAP = 3
EMPTY_REPLY = "Nothing new for you right now."
_NUMBER = {2: "two", 3: "three", 4: "four", 5: "five"}


def pull_enabled() -> bool:
    """``ZOE_PULL_NOT_PUSH`` - default ON, read per call. ``0`` / ``off`` / ``false`` / ``no``
    removes the tier, the welcome phrases and the orb state (the pending endpoint answers
    ``enabled: false``)."""
    return (os.environ.get("ZOE_PULL_NOT_PUSH", "") or "").strip().lower() not in _OFF


# ── what counts as a pull (whole-utterance, deterministic, no model) ──────────────────────
PULL_PHRASES = frozenset({
    "whats up", "whats up zoe",
    "anything for me", "anything new for me", "anything new", "anything pending",
    "anything waiting", "anything waiting for me", "anything i should know",
    "anything i need to know", "anything else for me",
    "have you got anything for me", "do you have anything for me", "got anything for me",
    "got anything", "is there anything for me", "is there anything new",
    "is there anything pending", "is there anything i need to know",
    "what have i got", "what have you got", "what have you got for me",
    "what do you have for me", "what have you got to tell me", "whats waiting",
    "whats waiting for me", "whats pending", "whats pending for me", "whats there for me",
})
# "what's new" / "what's happening" / "what's going on" are deliberately NOT here: they stay
# greetings (the day brief and the single raise still answer them, as the bar's S5 expects).
NOT_NOW = frozenset({"not now", "not right now", "not just now", "not now thanks",
                     "not now thank you"})
WELCOME = frozenset({"that was welcome", "that was helpful", "that was useful",
                     "that was welcome thanks", "that was helpful thanks"})
NEUTRAL = frozenset({"that was fine", "that was okay", "that was ok", "that was alright",
                     "that was all right"})
_TRAILERS = ("please", "then")


def _words(text: str | None) -> str:
    """Normalised utterance: lowercase, apostrophes dropped, punctuation to spaces, the name
    "zoe" and polite trailers removed, greeting leads ("hey", "morning") stripped."""
    from brief_first_turn import _GREETING_LEADS
    from conversation_opener import _normalize

    toks = [t for t in _normalize(text).split() if t != "zoe"]
    out = " ".join(toks)
    stripped = True
    while stripped and out:
        stripped = False
        for lead in _GREETING_LEADS:
            if out == lead or out.startswith(lead + " "):
                out = out[len(lead):].strip()
                stripped = True
                break
    while out:
        last = out.rsplit(" ", 1)[-1]
        if last in _TRAILERS and last != out:
            out = out[: -len(last)].strip()
        else:
            break
    return out


def is_pull(text: str | None) -> bool:
    return _words(text) in PULL_PHRASES


def classify(text: str | None) -> str | None:
    """``"pull"`` | ``"tap:<signal>"`` | None - one normalisation for the voice hot path (every
    utterance passes through here once the flag is on)."""
    w = _words(text)
    if w in PULL_PHRASES:
        return "pull"
    if w in NOT_NOW:
        return "tap:not_now"
    if w in WELCOME:
        return "tap:welcome"
    if w in NEUTRAL:
        return "tap:neutral"
    return None


def tap_signal(text: str | None) -> str | None:
    """``not_now`` | ``welcome`` | ``neutral`` for a whole-utterance tap phrase, else None. The
    caller binds it to a recent delivery; with none, the phrase is not ours."""
    w = _words(text)
    if w in NOT_NOW:
        return "not_now"
    if w in WELCOME:
        return "welcome"
    if w in NEUTRAL:
        return "neutral"
    return None


# ── the pending queue ─────────────────────────────────────────────────────────────────────
@dataclass
class Item:
    id: str
    kind: str
    source_ref: str
    text: str
    hint: str
    salience: float
    cues: str
    surfaced: int
    sensitivity: str = ""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _member(uid: str) -> bool:
    """A real (or harness-minted demo) member - never a guest sentinel."""
    from proactive.selector import _eligible
    from user_filters import GUEST_USERS

    uid = (uid or "").strip()
    return bool(uid) and uid not in GUEST_USERS and _eligible(uid)


async def pending_items(db, uid: str, now: datetime | None = None) -> list[Item]:
    """The selector's candidates it would still raise, highest salience first: not expired, out
    of cooldown, raised fewer than ``MAX_SURFACED`` times. A read - nothing is generated."""
    from proactive.selector import MAX_SURFACED

    now = now or _now()
    stamp = _iso(now)
    async with db.execute(
        "SELECT id, kind, source_ref, text, hint, salience, cue_words, expires_at, "
        "cooldown_until, surfaced_count FROM proactive_candidates WHERE user_id = ?", (uid,),
    ) as cur:
        rows = [tuple(r) for r in await cur.fetchall()]
    items: list[Item] = []
    for r in rows:
        if str(r[7]) <= stamp or (r[8] and str(r[8]) > stamp) or int(r[9] or 0) >= MAX_SURFACED:
            continue
        try:
            sal = float(r[5] or 0)
        except (TypeError, ValueError):
            sal = 0.0
        items.append(Item(str(r[0]), str(r[1]), str(r[2]), str(r[3] or ""), str(r[4] or ""), sal,
                          str(r[6] or ""), int(r[9] or 0)))
    items.sort(key=lambda i: (-i.salience, i.kind, i.id))
    sens = await lines.sensitivity_of(db, [i.id for i in items])
    for i in items:
        i.sensitivity = sens.get(i.id, "")
    return items


def _quiet(now: datetime) -> bool:
    try:
        from proactive.engine import _is_in_quiet_hours

        return bool(_is_in_quiet_hours(now))
    except Exception:  # noqa: BLE001
        return False


async def pending_state(uid: str, now: datetime | None = None) -> dict:
    """What the panel's orb may show: ``{enabled, count, top, quiet}``. ``top`` is the coarse
    class of the highest-priority item (``question`` | ``notify``) - never an item's words, kind
    or sensitivity. Guest, flag off, selector off or any error: ``count`` 0."""
    from proactive.selector import selector_enabled

    now = now or _now()
    state = {"enabled": False, "count": 0, "top": None, "quiet": _quiet(now)}
    if not pull_enabled() or not selector_enabled() or not _member(uid):
        return state
    state["enabled"] = True
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            items = await pending_items(db, uid, now)
    except Exception as exc:  # noqa: BLE001 - an orb poll must never error
        logger.debug("pull: pending read failed (non-fatal): %r", exc)
        return state
    state["count"] = len(items)
    state["top"] = lines.klass_of(items[0].kind) if items else None
    return state


# ── Zoe's voice (deterministic) ───────────────────────────────────────────────────────────
_EVENT_RE = re.compile(r"^(?P<title>.+?) \((?P<dow>Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
                       r"(?P<hm>\d{1,2}:\d{2})\)$")
_DOW = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
        "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}
_PERSON = (
    (re.compile(r"^(?:the )?user(?:'s|’s)\b\s*", re.I), "your "),
    (re.compile(r"^(?:the )?user is\b\s*", re.I), "you're "),
    (re.compile(r"^(?:the )?user was\b\s*", re.I), "you were "),
    (re.compile(r"^(?:the )?user has\b\s*", re.I), "you have "),
    (re.compile(r"^(?:the )?user had\b\s*", re.I), "you had "),
    (re.compile(r"^(?:the )?user will\b\s*", re.I), "you'll "),
    (re.compile(r"^(?:the )?user\b\s*", re.I), "you "),
)


def second_person(text: str) -> str:
    """Stored loop / moment text is third person ("User is anxious about …"): speak it to them
    ("you're anxious about …"). Anything else is returned with its first letter lowered."""
    t = (text or "").strip().rstrip(".!")
    for rx, sub in _PERSON:
        if rx.match(t):
            return sub + rx.sub("", t, count=1)
    return t[:1].lower() + t[1:] if t else t


def sentence(item: Item) -> str:
    """One item in Zoe's voice. An event is told; a loop or a moment is asked after. The hint is
    used only when it already reads as a question (the selector's default hint is an INSTRUCTION
    to the brain, never speakable)."""
    if item.kind == "event":
        m = _EVENT_RE.match(item.text.strip())
        if m:
            return f"You've got {m.group('title')} on {_DOW[m.group('dow')]} at {m.group('hm')}."
        return f"You've got {item.text.strip().rstrip('.')}."
    hint = item.hint.replace('"', "").strip()
    if hint.endswith("?") and len(hint) <= 160:
        return f"I wanted to ask: {hint}"
    ask = "how is that going now?" if item.kind == "emotional" else "how's that going?"
    return f"You mentioned {second_person(item.text)} - {ask}"


def _cont(s: str) -> str:
    return s if s.startswith(("I ", "I'")) else s[:1].lower() + s[1:]


def compose(items: list[Item], *, more: int = 0, private: int = 0, spoken: bool = False) -> str:
    """The reply for a pull that delivers ``items`` (highest priority first)."""
    if not items:
        return (EMPTY_REPLY if not private else
                "I've got something private for you - ask me in the chat.")
    parts = [sentence(i) for i in items]
    if len(parts) == 1:
        text = parts[0]
    else:
        head = f"I've got {_NUMBER.get(len(parts), str(len(parts)))} things for you."
        text = " ".join([head, parts[0]] + [f"Also, {_cont(p)}" for p in parts[1:]])
    if more:
        text += (f" I've got {more} more - ask me again and I'll go on." if spoken
                 else f" And {more} more.")
    if private:
        text += " I've also got something private for you - ask me in the chat."
    return text


def welcome_component() -> dict:
    """The chat's one-tap row (``zoe.component``): each button sends its words as the next turn,
    which the tap phrases above catch. Nothing here names an item."""
    return {"component": "pull_feedback", "props": {"title": "Was that welcome?"},
            "actions": [{"label": "Welcome", "query": "That was welcome"},
                        {"label": "Fine", "query": "That was fine"},
                        {"label": "Not now", "query": "Not now", "kind": "warn"}]}


# ── the pull ──────────────────────────────────────────────────────────────────────────────
@dataclass
class PullResult:
    reply: str
    delivered: int = 0
    kinds: list[str] = field(default_factory=list)
    ui: dict | None = None


async def pulled_refs(uid: str, local_now: datetime) -> set[str]:
    """Selector keys (``open_loops:<id>`` / ``memory:<id>`` / ``events:<id>``) the member pulled
    since local midnight, from the delivery-ledger lines. ``set()`` with the feature or the
    ledger off, or when the table is not there."""
    if not pull_enabled() or not lines.lines_enabled():
        return set()
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            async with db.execute(
                "SELECT source_ref FROM proactive_ledger_lines WHERE user_id = ? "
                "AND line = 'pulled' AND created_at >= ?", (uid, _iso(midnight)),
            ) as cur:
                return {str(r[0]) for r in await cur.fetchall()}
    except Exception as exc:  # noqa: BLE001
        logger.debug("pull: pulled refs unreadable (non-fatal): %r", exc)
        return set()


async def _brief_will_fire(uid: str, now: datetime) -> bool:
    """True when the morning brief is about to ride this member's next greeting-shaped turn - a
    pull that found nothing must then hand the turn on instead of masking it."""
    try:
        import brief_first_turn as b
        from time_utils import zoe_timezone

        if not b.brief_on_first_turn_enabled() or b.is_synthetic_user(uid):
            return False
        local = now.astimezone(zoe_timezone())
        if not b.in_window(local) or b._settled.get(uid) == local.date().isoformat():
            return False
        return await b._claim_state(uid, now) == "free"
    except Exception:  # noqa: BLE001
        return False


async def pull(uid: str, session_id: str, *, channel: str = "chat",
               speaker_verified: bool | None = None,
               now: datetime | None = None) -> PullResult | None:
    """Deliver everything pending once. ``None`` = not ours (flag off, selector off, a guest,
    a brief about to speak, or an error): the caller falls through to the router and the
    brain exactly as before. Never raises."""
    from proactive.selector import COOLDOWN, selector_enabled

    if not pull_enabled() or not selector_enabled() or not _member(uid):
        return None
    now = now or _now()
    spoken = channel in _SPOKEN_CHANNELS
    try:
        from db_compat import get_compat_db
        from proactive import ledger

        async with get_compat_db() as db:
            items = await pending_items(db, uid, now)
            if not items:
                if await _brief_will_fire(uid, now):
                    return None
                return PullResult(EMPTY_REPLY)
            # The restraint tier (when it exists): a sensitive item is not SPOKEN to an
            # unverified voice; it stays pending and chat offers it.
            held = [i for i in items if i.sensitivity and spoken and speaker_verified is False]
            ready = [i for i in items if i not in held]
            take = ready[:SPOKEN_CAP] if spoken else ready
            delivered: list[Item] = []
            stamp, cool = _iso(now), _iso(now + COOLDOWN)
            for i in take:
                # Compare-and-set on the count we read: two overlapping asks deliver once.
                cur = await db.execute(
                    "UPDATE proactive_candidates SET surfaced_count = surfaced_count + 1, "
                    "cooldown_until = ?, last_surfaced_session = ?, last_surfaced_at = ? "
                    "WHERE id = ? AND surfaced_count = ?",
                    (cool, session_id, stamp, i.id, i.surfaced))
                if (getattr(cur, "rowcount", 1) or 0) > 0:
                    delivered.append(i)
            for i in delivered:
                await ledger.record(
                    db, user_id=uid, candidate_id=i.id, kind=i.kind, source_ref=i.source_ref,
                    shape="pull", delivered_by="pull", session_id=session_id, cue_words=i.cues,
                    now=now, outcome="accepted", voiced=1)
                await lines.record(
                    db, user_id=uid, line="pulled", kind=i.kind, source_ref=i.source_ref,
                    reason="asked", score=i.salience, shape="pull", channel=channel,
                    session_id=session_id, sensitivity=i.sensitivity, now=now)
            for i in held:
                await lines.record(
                    db, user_id=uid, line="withheld", kind=i.kind, source_ref=i.source_ref,
                    reason="sensitivity_unverified", score=i.salience, shape="pull",
                    channel=channel, session_id=session_id, sensitivity=i.sensitivity, now=now)
    except Exception as exc:  # noqa: BLE001 - a pull must never break a turn
        logger.warning("pull: failed for user=%s: %r", uid, exc)
        return None
    if not delivered and not held:
        return None  # lost the race to a concurrent ask: let the turn go on
    more = max(0, len(ready) - len(delivered)) if spoken else 0
    logger.info("PULL user=%s channel=%s delivered=%d more=%d private=%d", uid, channel,
                len(delivered), more, len(held))
    reply = compose(delivered, more=more, private=len(held), spoken=spoken)
    ui = {"kind": "pull", "zoe_component": welcome_component()} if (delivered and not spoken) else {"kind": "pull"}
    return PullResult(reply, len(delivered), [i.kind for i in delivered], ui)


# ── the tap, as a turn ────────────────────────────────────────────────────────────────────
_TAP_REPLY = {
    "welcome": "Glad that helped.",
    "neutral": "Noted.",
}


async def tap(uid: str, text: str, *, channel: str = "chat",
              now: datetime | None = None) -> str | None:
    """"not now" / "that was welcome" / "that was fine" right after a raise or a pull: record it
    and say so. ``None`` when the phrase is not a tap, the feature is off, or there is no recent
    delivery to tap about (then it is an ordinary utterance and goes on to the router)."""
    signal = tap_signal(text)
    if signal is None or not pull_enabled() or not lines.lines_enabled() or not _member(uid):
        return None
    wrote = await lines.record_tap(uid, signal, channel=channel, now=now)
    if not wrote:
        return None
    if signal == "not_now":
        return ("Okay, I'll hold off on those." if lines.backoff_active() else "Okay, noted.")
    return _TAP_REPLY[signal]
