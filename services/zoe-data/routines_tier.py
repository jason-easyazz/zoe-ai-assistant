"""Routines, stage 1: the conversation tier (voice and chat). Store: ``routines_store``; record ``docs/knowledge/routines.md``.

One deterministic tier in ``fast_tiers._resolve_tiers`` (the conversation half, which voice runs FIRST with ``phase="conversation"``).
Flag-dark twice: ``ZOE_ROUTINES`` (master, off | on) AND ``ZOE_ROUTINE_VOICE`` (off | shadow | on), both default off and read per
call. Off = None before ANY work (no floor call, no database, no regex), so every turn is answered exactly as today. Shadow = a
``ROUTINE_TIER shadow would=<kind>`` line from read-only checks, and None: it claims nothing and writes nothing.

WHAT IT TAKES (whole utterances only; everything else is None, so the reminder, list, scene and brain paths answer unchanged):
  * inside a WALK (Zoe named a step to this member in this session < 120 s ago, ``routines_store.named_step``): "done" / "yep" /
    "had them" ticks THAT step, "skip" skips it, "later" / "not now" snoozes it, "stop" closes the walk, "no I haven't" / "untick
    that" right after a change undoes it. Outside a walk none of these is taken (a bare "yep" ticks nothing).
  * "what's next?" / "what's left?" names the next line of the walk, or of the member's routine whose window is open now;
    "start my <name> routine" opens a walk on that routine. No routine = None.
  * a ONE-OFF: "remind me to take|bring|pack|grab X to work tomorrow", "don't let me forget X tomorrow morning", "put X on my
    morning routine (for tomorrow)", "add X to tomorrow's morning (routine)" becomes one line on ONE day's copy ONLY when: the
    member has exactly one enabled routine of their own matching the cue (to work / to school / with me / when I leave / before I
    go / in the morning -> leaving, waking, breakfast; with dinner -> dinner; at bedtime -> bed; a named routine), the day is ONE
    day (today / tomorrow / a weekday), no clock time or number is said, no repeat is said, and the routine runs that day.
    Anything else steps aside, so "remind me at 7 to take the drill" stays an ordinary 7:00 reminder.
  * "I'm off" / "heading out" with an undone take-with line today: "Before you go: take the drill." (it names the line, so "got
    it" ticks it). Several lines: all are listed and NONE is named (a "got it" ticks nothing). Nothing undone = None.
  * a one-off that fits SEVERAL of the member's routines: "Which routine: Morning or Leaving?" and nothing is added.

THE WALK (``routines_store.note_named``) is keyed by the session, holds the member it was named to and the day copy's id (which
carries the household date), and is honoured only for that member, that day and ``WALK_S``. It survives only a turn that named
exactly one step in that turn (or asked the same speaker to say it again in a known voice): any other reply, a step-aside, a list
of several lines or a different speaker's turn closes it.

SAFETY. ``medical_safety.detect`` runs before anything is claimed: when it fires (an ingestion - "I took them twice by accident" -, a
red flag, an emergency-number question, a glucose reading) the tier returns None, the step is left alone, and the floor answers on
the brain lane exactly as today. The child safety floor, where it exists, is consulted the same way. A ``medicine`` line is ticked by
voice only for a VERIFIED speaker (``speaker_verified is True``) or on chat (the member's own signed-in session); otherwise Zoe says
so and points to the panel, and nothing is ticked. A dry run (``allow_writes=False``, the replay harness) takes nothing. Zoe never
speaks first: every reply here answers words the person just said.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Optional

import routines_store as rs

logger = logging.getLogger(__name__)

TIER = "routines"
ENV_VOICE = "ZOE_ROUTINE_VOICE"
MEDICINE_UNVERIFIED = "I can only tick that one when I know it's you. Say it again so I know your voice, or tap it on the panel."

_FILLER = r"(?:(?:ok|okay|right|yes|yep|so|and|um|uh|oh|zoe|hey|please)[\s,]+)*"
_TAIL = r"(?:[\s,]+(?:zoe|please|thanks|thank you|then|now))*"


def _rx(body: str) -> re.Pattern:
    return re.compile(r"^\W*" + _FILLER + body + _TAIL + r"\W*$", re.IGNORECASE)


_BARE = (
    ("done", _rx(r"(?:done|did it|i did it|i(?:'|’)?ve done it|i have done it|yes|yep|yeah|yup|had them|had it|took them|took it|"
                 r"i took them|i took it|finished|got it|tick it|tick that|ticked)")),
    ("skip", _rx(r"(?:skip|skip it|skip that|skip that one|not today)")),
    ("snooze", _rx(r"(?:later|not now|in a minute|in a bit|in ten minutes|remind me later|later please)")),
    ("stop", _rx(r"(?:stop|that(?:'|’)?s enough|that(?:'|’)?s all|stop the routine)")),
    ("undo", _rx(r"(?:no,? i haven(?:'|’)?t|no,? i have not|no,? i didn(?:'|’)?t|untick that|untick it|undo that)")),
)
_NEXT = _rx(r"(?:what(?:'|’)?s|what is|whats) (?:next|left)(?: on my (?:list|routine))?|next|next one|next step")
_LEFT = re.compile(r"\bleft\b", re.I)
_START = _rx(r"(?:start|begin|do|let(?:'|’)?s do) my (?P<name>[a-z][a-z ]{1,30}?) routine")
_LEAVING = _rx(r"(?:i(?:'|’)?m|i am|im) (?:off|leaving|heading out|going now|off to work|off to school)|heading out|"
               r"(?:bye,? )?see you later|bye,? i(?:'|’)?m off|off to work|off to school")

_CUES = (
    ("to work", ("leaving", "waking", "breakfast")), ("to school", ("leaving", "waking", "breakfast")),
    ("with me", ("leaving", "waking", "breakfast")), ("when i leave", ("leaving", "waking", "breakfast")),
    ("before i go", ("leaving", "waking", "breakfast")), ("in the morning", ("waking", "breakfast", "leaving")),
    ("morning", ("waking", "breakfast", "leaving")), ("with dinner", ("dinner",)), ("at bedtime", ("bed",)),
    ("before bed", ("bed",)),
)
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_REST_WORD = r"(?:to work|to school|with me|when i leave|before i go|in the morning|with dinner|at bedtime|before bed|tomorrow morning|" \
             r"tomorrow|today|tonight|morning|on (?:" + "|".join(_DAYS) + r")|" + "|".join(_DAYS) + r")"
_REST = r"(?P<rest>(?:\s+" + _REST_WORD + r")+)"
_ONE_OFF = (
    ("verb", _rx(r"(?:remind me to|don(?:'|’)?t let me forget to|dont let me forget to|remember to) (?P<verb>take|bring|pack|grab) "
                 r"(?P<obj>.+?)" + _REST)),
    ("obj", _rx(r"(?:don(?:'|’)?t|dont) let me forget (?P<obj>(?!to\b).+?)" + _REST)),
    ("named", _rx(r"(?:put|add) (?P<obj>.+?) (?:on|to) my (?P<name>[a-z][a-z ]{1,30}?) routine(?: for (?P<day>tomorrow|today|"
                  + "|".join(_DAYS) + r"))?")),
    ("named", _rx(r"(?:put|add) (?P<obj>.+?) (?:on|to) (?P<day>tomorrow|today|" + "|".join(_DAYS) + r")(?:'|’)?s "
                  r"(?P<name>[a-z][a-z ]{1,30}?)(?: routine)?")),
)
#: a clock time, a duration or any number = the person asked for a clock reminder: never a routine line
_CLOCK = re.compile(r"\d|o(?:'|’)?clock|\b(?:a\.?m\.?|p\.?m\.?|noon|midnight|half past|quarter (?:past|to))\b|"
                    r"\b(?:at|by|around|before|after) (?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b|"
                    r"\bin (?:a|an|one|two|three|five|ten|fifteen|twenty|thirty|a few|half an) (?:minute|minutes|hour|hours)\b", re.I)
_REPEAT = re.compile(r"\b(?:every|each|daily|weekdays|weekends|weekly|always|all week)\b", re.I)


def mode() -> str:
    if not rs.enabled():
        return "off"
    from typed_env import flag

    m = str(flag(ENV_VOICE) or "off").strip().lower()
    return m if m in ("shadow", "on") else "off"


# -- the words (pure) --------------------------------------------------------------------------------------------------------------
def shape(text: str) -> Optional[tuple[str, dict]]:
    """``(kind, parts)`` for a recognised utterance, else None. Kinds: done | skip | snooze | stop | undo | next | start |
    leaving | one_off. Pure."""
    t = (text or "").strip()
    if not t or len(t) > 160:
        return None
    for kind, rx in _BARE:
        if rx.match(t):
            return kind, {}
    if _NEXT.match(t):
        return "next", {"left": bool(_LEFT.search(t)), "bare": not re.search(r"\bwhat", t, re.I)}
    m = _START.match(t)
    if m:
        return "start", {"name": m.group("name").strip()}
    if _LEAVING.match(t):
        return "leaving", {}
    for form, rx in _ONE_OFF:
        m = rx.match(t)
        if m:
            g = m.groupdict()
            obj = (g.get("obj") or "").strip()
            line = f"{g['verb']} {obj}" if form == "verb" else obj
            return "one_off", {"line": line, "rest": (g.get("rest") or "").strip().lower(), "name": (g.get("name") or "").strip(),
                               "day": (g.get("day") or "").strip().lower()}
    return None


def one_off_cue(parts: dict, today: date) -> Optional[tuple[date, tuple[str, ...], str]]:
    """``(day, anchors, routine name)`` when a one-off's words name ONE day and a routine moment, else None (step aside)."""
    if _CLOCK.search(parts["line"] + " " + parts["rest"]) or _REPEAT.search(parts["line"] + " " + parts["rest"]):
        return None
    rest = parts["rest"]
    days = re.findall(r"\b(tomorrow|today|tonight|" + "|".join(_DAYS) + r")\b", rest + " " + parts.get("day", ""))
    if len(set(days)) > 1:
        return None
    word = days[0] if days else ("" if parts["name"] else None)
    if word is None:
        return None
    if word in ("today", "tonight"):
        day = today
    elif word == "tomorrow":
        day = today + timedelta(days=1)
    elif word:
        day = today + timedelta(days=((_DAYS.index(word) - today.weekday() - 1) % 7) + 1)
    else:
        day = None                                         # a named routine with no day: decided against its window
    anchors: tuple[str, ...] = ()
    for cue, a in _CUES:
        if re.search(r"\b" + cue + r"\b", rest):
            anchors = a
            break
    if not anchors and not parts["name"]:
        return None
    return day, anchors, parts["name"]


# -- helpers -----------------------------------------------------------------------------------------------------------------------
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


def _floor_owns(text: str, session_id: str, user_id: str) -> bool:
    """A safety floor would answer this turn on the brain lane: step aside (it runs AFTER the fast tiers, so a claim here would
    bypass it). Fail closed: a floor that errors owns the turn."""
    try:
        import medical_safety

        if medical_safety.detect(text) != medical_safety.NONE:
            return True
    except Exception:  # noqa: BLE001
        return True
    try:
        import child_safety_floor  # type: ignore[import-not-found]  # not on every branch yet
    except ImportError:
        return False
    try:
        return child_safety_floor.check(text, session_id, user_id, dry=True) is not None
    except Exception:  # noqa: BLE001
        return True


def _spoken_name(name: str) -> str:
    n = name if re.search(r"\broutine$", name, re.I) else f"{name} routine"
    return n[0].lower() + n[1:] if n[1:2].islower() or len(n) == 1 else n


def _listed(texts: list[str]) -> str:
    return texts[0] if len(texts) == 1 else ", ".join(texts[:-1]) + " and " + texts[-1]


def _day_label(day: date, today: date) -> str:
    return "today's" if day == today else "tomorrow's" if day == today + timedelta(days=1) else f"{day.strftime('%A')}'s"


async def _own(member: str) -> list[dict]:
    return [r for r in await rs.list_routines(member) if r["user_id"] == member and r["enabled"]]


def _open(steps: list[dict], after: Optional[str] = None) -> list[dict]:
    """Lines still to do, pending first then snoozed, skipping ``after`` (the one just acted on)."""
    rest = [s for s in steps if s["id"] != after]
    return [s for s in rest if s["state"] == "pending"] + [s for s in rest if s["state"] == "snoozed"]


async def _day_of_step(member: str, ds_id: str) -> Optional[dict]:
    rid = ds_id.split(":", 1)[0]
    return await rs.day_copy(member, rid)


# -- the answers -------------------------------------------------------------------------------------------------------------------
async def _name_next(session_id: str, member: str, day: dict, *, after: Optional[str] = None, lead: str = "Next: ") -> Optional[str]:
    todo = _open(day["steps"], after)
    if not todo:
        return None
    await rs.note_named(session_id, member, todo[0]["id"])
    return f"{lead}{todo[0]['text']}."


def _summary(day: dict) -> str:
    left = [s["text"] for s in day["steps"] if s["state"] in ("pending", "snoozed")]
    name = _spoken_name(day["routine"]["name"])
    return f"That's your {name} done." if not left else f"That's your {name} done, except {_listed(left)}."


#: session -> (member, the day-line IDS a reply just listed, when): a "got it" right after a list of several is asked about, never
#: ticked. IDS ONLY, never text; bounded and aged on every write and read (``_prune``); cleared by both erasers (``forget_member``).
_LISTED: "dict[str, tuple]" = {}
_MAX_LISTED = 256


def _prune() -> None:
    now = rs._mono()
    for k in [k for k, v in _LISTED.items() if now - v[2] > rs.WALK_S]:
        _LISTED.pop(k, None)
    while len(_LISTED) > _MAX_LISTED:
        _LISTED.pop(next(iter(_LISTED)), None)


def _remember_listed(session_id: str, member: str, lines: list[dict]) -> None:
    _prune()
    _LISTED.pop(session_id, None)
    _LISTED[session_id] = (member, [s["id"] for s in lines], rs._mono())
    _prune()


def listed_entries() -> dict:
    """The live entries (aged first): what a forget or a test may inspect."""
    _prune()
    return dict(_LISTED)


def forget_member(member: str) -> None:
    """The erasers' hook (``routines_store._forget_caches``, member AND entity forget): nothing of ``member``'s stays listed."""
    for k in [k for k, v in _LISTED.items() if v[0] == member]:
        _LISTED.pop(k, None)


async def _which_line(session_id: str, member: str) -> Optional[str]:
    _prune()
    listed = _LISTED.pop(session_id or "", None)
    if listed is None or listed[0] != member:
        return None
    lines = await rs.live_lines(member, listed[1])        # re-read NOW, by id, under the gate, shielded: never a cached word
    if len(lines) < 2:
        return None
    texts = [s["text"] for s in lines]
    return "Which one: " + ", ".join(texts[:-1]) + " or " + texts[-1] + "?"


async def _bare(kind: str, session_id: str, member: str, verified: bool) -> Optional[str]:
    if kind == "undo":
        try:
            out = await rs.undo_said(session_id, member, verified=verified)
        except rs.StoreError as exc:
            return None if exc.code == "nothing_to_undo" else MEDICINE_UNVERIFIED if exc.code == "unverified" else None
        day = await _day_of_step(member, out["id"])
        text = next((s["text"] for s in (day or {}).get("steps", []) if s["id"] == out["id"]), "that")
        return f"OK, it's back on the list: {text}."
    ds_id = rs.named_step(session_id, member)
    if ds_id is None:
        return (await _which_line(session_id, member)) if kind == "done" else None   # no walk: a bare "yep" is not a tick
    if kind == "stop":
        rs.close_walk(session_id)
        return "OK."
    try:
        out = await rs.act_said(session_id, member, kind, verified=verified)
    except rs.StoreError as exc:
        if exc.code == "unverified":
            return MEDICINE_UNVERIFIED
        rs.close_walk(session_id)
        return None if exc.code in ("nothing_named", "not_found") else "That one's already sorted."
    day = await _day_of_step(member, out["id"])
    if day is None:
        return "Done."
    head = {"done": "Ticked.", "skip": "Skipped.", "snooze": "OK, I'll keep it on the list."}[kind]
    nxt = await _name_next(session_id, member, day, after=out["id"], lead=" Next: ")
    return head + nxt if nxt else f"{head} {_summary(day)}" if kind != "snooze" else head


async def _current(member: str) -> Optional[dict]:
    """The member's routine whose window is open now with something left to do (the first, by creation)."""
    for r in await _own(member):
        if rs.in_window(r):
            day = await rs.day_copy(member, r["id"])
            if day and _open(day["steps"]):
                return day
    return None


async def _next(parts: dict, session_id: str, member: str) -> Optional[str]:
    ds_id = rs.named_step(session_id, member)
    if ds_id is None and parts.get("bare"):
        return None                                        # a bare "next" outside a walk is a music skip, not ours
    day = await _day_of_step(member, ds_id) if ds_id else await _current(member)
    if day is None:
        return None
    todo = _open(day["steps"])
    if not todo:
        return _summary(day)
    if parts.get("left") and len(todo) > 1:
        rs.close_walk(session_id)                          # a list of several names NONE: a "done" is asked about, never ticked
        _remember_listed(session_id, member, todo)
        return f"{len(todo)} things left: {_listed([s['text'] for s in todo])}."
    await rs.note_named(session_id, member, todo[0]["id"])
    if parts.get("left"):
        return f"One thing left: {todo[0]['text']}."
    return f"Next: {todo[0]['text']}."


async def _start(parts: dict, session_id: str, member: str) -> Optional[str]:
    want = parts["name"].lower()
    hits = [r for r in await _own(member) if want in (r["name"].lower(), r["anchor"]) or r["name"].lower().startswith(want)]
    if len(hits) != 1:
        return None
    day = await rs.day_copy(member, hits[0]["id"])
    if day is None:
        return None
    todo = _open(day["steps"])
    if not todo:
        return _summary(day)
    await rs.note_named(session_id, member, todo[0]["id"])
    n = len(todo)
    return f"{hits[0]['name']}: {'one thing' if n == 1 else f'{n} things'}. First, {todo[0]['text']}."


async def _leaving(session_id: str, member: str) -> Optional[str]:
    lines: list[dict] = []
    for r in await _own(member):
        day = await rs.day_copy(member, r["id"])
        if day:
            lines += [s for s in _open(day["steps"]) if s["kind"] == "take_with"]
    if not lines:
        return None
    if len(lines) == 1:
        await rs.note_named(session_id, member, lines[0]["id"])
    else:
        rs.close_walk(session_id)                          # several lines: no single target, so a "got it" ticks nothing ...
        _remember_listed(session_id, member, lines)       # ... and Zoe asks which (ids only)
    return f"Before you go: {_listed([s['text'] for s in lines])}."


async def _pick_routine(member: str, cue: tuple, today: date):
    """``(routine, day)`` when exactly ONE of the member's routines fits the cue; ``("ask", routines)`` when several do (never a
    guess: Zoe asks which, by the routines' own names); None when none does (the reminder path answers)."""
    day, anchors, name = cue
    mine = await _own(member)
    if name:
        n = name.lower()
        hits = [r for r in mine if n in (r["name"].lower(), r["anchor"])]
    else:
        hits = [r for r in mine if r["anchor"] in anchors]          # EVERY routine the cue fits, not the first anchor's
    if len(hits) > 1:
        return "ask", hits
    if not hits:
        return None
    r = hits[0]
    if day is None:
        day = today if rs.household_now().strftime("%H:%M") <= rs.window_of(r)[1] else today + timedelta(days=1)
    return (r, day) if rs.runs_on(r, day) else None


def _which(routines: list[dict]) -> str:
    names = [r["name"] for r in routines]
    return "Which routine: " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]) + "?"


async def _one_off(parts: dict, member: str, *, channel: str, write: bool) -> Optional[str]:
    today = rs.household_today()
    cue = one_off_cue(parts, today)
    if cue is None:
        return None
    picked = await _pick_routine(member, cue, today)
    if picked is None:
        return None
    if picked[0] == "ask":
        return "would" if not write else _which(picked[1])     # nothing is added until the person names one
    r, day = picked
    if not write:
        return "would"
    try:
        await rs.add_one_off(member, r["id"], day, parts["line"], created_via=channel)
    except rs.StoreError:
        return None                                        # refused (past day, too many lines): today's reminder path answers
    return f"Added to {_day_label(day, today)} {_spoken_name(r['name'])}: {parts['line']}."


async def answer(kind: str, parts: dict, session_id: str, member: str, *, verified: bool, channel: str) -> Optional[str]:
    if kind in ("done", "skip", "snooze", "stop", "undo"):
        return await _bare(kind, session_id, member, verified)
    if kind == "next":
        return await _next(parts, session_id, member)
    if kind == "start":
        return await _start(parts, session_id, member)
    if kind == "leaving":
        return await _leaving(session_id, member)
    return await _one_off(parts, member, channel=channel, write=True)


async def _would(kind: str, parts: dict, session_id: str, member: str) -> bool:
    """Shadow: would the tier claim this? Read-only (no day copy is made, nothing is named)."""
    if kind in ("done", "skip", "snooze", "stop") or (kind == "next" and parts.get("bare")):
        return rs.walk_open(session_id, member)
    if kind == "one_off":
        return (await _one_off(parts, member, channel="shadow", write=False)) == "would"
    mine = await _own(member)
    if kind == "start":
        return any(parts["name"].lower() in (r["name"].lower(), r["anchor"]) for r in mine)
    return any(rs.in_window(r) for r in mine)


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None,
               extra_ctx: Optional[dict[str, Any]] = None, allow_writes: bool = True):
    """The fast-tier hook: a ``DispatchResult`` or None. Off = None before any work. NEVER raises."""
    try:
        m = mode()
        if m == "off":
            return None
        ch = (channel or "").strip().lower()
        if ch not in ("voice", "chat") or not allow_writes:
            return None
        sh = shape(text)
        if sh is None or sh[0] != "done":
            listed = _LISTED.get(session_id or "")
            if listed is not None and (sh is None or sh[0] not in ("leaving", "next")):
                _LISTED.pop(session_id or "", None)        # the list is answered only by the very next turn
        if sh is None:
            rs.close_walk(session_id)                      # any other turn ends the walk: a later "yes" answers THAT turn
            return None
        if _floor_owns(text, session_id, user_id):
            rs.close_walk(session_id)
            logger.info("ROUTINE_TIER stepped_aside=floor kind=%s", sh[0])
            return None
        member = _member(user_id, ch, extra_ctx)
        if member is None:
            return None
        kind, parts = sh
        if m == "shadow":
            if await _would(kind, parts, session_id, member):
                logger.info("ROUTINE_TIER shadow would=%s channel=%s", kind, ch)
            return None
        verified = ch == "chat" or (extra_ctx or {}).get("speaker_verified") is True
        before = rs.walk_entry(session_id)
        reply = await answer(kind, parts, session_id, member, verified=verified, channel=ch)
        if not reply or (rs.walk_entry(session_id) is before and reply != MEDICINE_UNVERIFIED):
            rs.close_walk(session_id)      # THE walk rule: it survives only a turn that named ONE step just now (or asked to retry it)
        if not reply:
            return None
        import expert_dispatch as _xd

        logger.info("ROUTINE_TIER claimed=%s channel=%s verified=%d", kind, ch, int(verified))
        return _xd.DispatchResult(domain="routines", reply=reply, intent=f"routine_{kind}", tier=TIER)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ROUTINE_TIER failed (non-fatal): %s", type(exc).__name__)
        return None
