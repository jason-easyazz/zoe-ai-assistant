"""Listen pace: Zoe tunes her own end-of-turn wait from what happens after she closes a recording (the pure decision core).

The owner's question was "could she auto adjust this setting?". This module is the controller that answers it, and nothing
else: no I/O, no clock reads (every function takes ``now``), no import of the rest of zoe-data, stdlib only (the lab replayer
under ``labs/listen-pace/`` loads it by path). SSOT: ``docs/knowledge/voice-listen-pace.md``.

The rule, in one sentence each:

* one integer LEVEL per subject, ``L-3 .. L+4`` in 80 ms steps (one VAD chunk); L0 is today's household default (deep tail
  640 ms, quiet tail 800 ms, clean-tail gate off). Shorter levels turn on the clean-tail gate (560 / 640 / 720 ms) and never
  touch the deep tail; longer levels raise the deep and quiet tails (``PROFILES``);
* a subject is ``(panel, verified member)`` or ``(panel, household)``; an unknown speaker always gets the panel's household
  level (``level_for``);
* LENGTHEN FAST: two cut events in the last 20 eligible turns, or one cut right after a reflex sound, is one level longer at
  once; an explicit "you keep cutting me off" is two;
* SHORTEN SLOW: one level shorter only after 40 consecutive clean eligible turns (80 after any lengthen, 120 to return to a level
  that caused a lengthen in the last 30 days, at most one automatic shortening a day); any cut resets the run;
* inside a floor and a ceiling: ``[LEVEL_MIN, LEVEL_MAX]``, and a child subject's floor is L0 (role-based, never voice-inferred);
* UNKNOWN NEVER SHORTENS: any malformed, missing or contradictory input leaves the state exactly as it was (a cut-off command is
  the bug the owner cares about; an over-long wait is merely slow). ``step`` applies the parser's own validation
  (``valid_outcome``), so a hand-built ``Outcome`` is held to the same rules;
* ONLY PROVEN EVIDENCE SHORTENS: a clean turn counts toward the run only when it proves the SHORTER level's close would have
  been clean too (``proves_clean_at``: the observed level known and at least that long, and no in-turn pause, ``pause_max_ms``,
  that the shorter level would have closed on). Unproven is neutral. A cut needs no such proof (lengthening is the safe
  direction). The panel's FIRST_SOUND line does not carry ``pause_max_ms`` yet, so automatic shortening is dormant until it does.

A "cut event" is the person talking after the close: ``still_talking=1`` on the daemon's FIRST_SOUND line (>= 3 speech chunks
inside the fixed 1.5 s window after the close), weight 1; the server's trailing-"and" hold merging a continuation, weight 0.5.

Flag: ``ZOE_LISTEN_PACE`` = off (default) | shadow | on, read by ``mode()``. Nothing in this module acts on it; the callers
(the persistence/wire layer, not wired yet) do.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Mapping, Optional

# ── constants (design note section 3) ───────────────────────────────────────────────────────────────────────────────────────
STEP_MS = 80                 # one VAD chunk (CHUNK_SIZE 1280 at 16 kHz); the daemon ceil-rounds tails, so finer is fiction
LEVEL_MIN = -3
LEVEL_MAX = 4
DEFAULT_LEVEL = 0
CHILD_DEFAULT = 1            # a child starts one step slower ...
CHILD_FLOOR = 0              # ... and never goes below the household default
WINDOW = 20                  # cut events are counted over the last this-many eligible turns
EVENTS_TO_LENGTHEN = 2       # this many cut events inside WINDOW lengthen one level
RUN_SHORTEN = 40             # consecutive clean eligible turns before one level shorter
RUN_AFTER_LENGTHEN = 80      # ... after any lengthen
RUN_RETURN_TO_BAD = 120      # ... to return to a level that triggered a lengthen inside BAD_LEVEL_DAYS
RUN_REFLEX_FACTOR = 2        # where the reflex is on, every run requirement doubles
BAD_LEVEL_DAYS = 30
STEP_GAP_S = 86400.0         # at most one AUTOMATIC shortening per this many seconds (lengthening is never rate-limited)
DECAY_IDLE_DAYS = 21         # a SHORTER-than-default level drifts back up one step a week after this long unheard
DECAY_EVERY_DAYS = 7
PIN_DAYS = 90                # a spoken "give me more time" holds this long against automatic shortening
QUICKER_GUARD_TURNS = 20     # a cut inside this many eligible turns after "you can be quicker" undoes it silently
MIN_SPEECH_MS = 480          # a shorter turn says nothing about the tail (the clean gate's own minimum)
MIN_OBS_MS = 1200            # a CLEAN verdict needs the post-close window observed at least this long
STILL_TALKING_CHUNKS = 3     # the daemon's still_talking=1 is exactly ">= this many speech chunks in the post-close window"
HALF = 2                     # cut weights are stored in half-events: a still_talking cut = 2, a merged continuation = 1
DAY_S = 86400.0

#: level -> (deep_ms, quiet_ms, clean_ms); clean 0 = the clean-tail gate is off
PROFILES = {
    -3: (640, 800, 560),
    -2: (640, 800, 640),
    -1: (640, 800, 720),
    0: (640, 800, 0),
    1: (720, 880, 0),
    2: (800, 960, 0),
    3: (880, 1040, 0),
    4: (960, 1200, 0),
}

CLOSE_RULES = ("deep", "quiet", "clean", "hesitation")     # a close on SILENCE; cap / no_speech / other are not evidence
WINDOWS = ("wake", "followup", "conversation", "barge")
FREEZES = ("media", "night", "noise", "first_wake", "speculation", "cap")
OVERRIDES = ("longer", "quicker", "normal")
MODES = ("off", "shadow", "on")


def mode() -> str:
    """``ZOE_LISTEN_PACE`` (off | shadow | on), read at call time through the registry; anything else is ``off``."""
    try:
        from typed_env import flag  # lazy: the module stays importable with nothing from zoe-data on the path

        m = str(flag("ZOE_LISTEN_PACE")).strip().lower()
    except Exception:  # noqa: BLE001 - a broken registry read is "off", never an exception on the voice path
        return "off"
    return m if m in MODES else "off"


@dataclass(frozen=True)
class Profile:
    deep_ms: int
    quiet_ms: int
    clean_ms: int

    def as_frame(self) -> dict:
        return {"deep": self.deep_ms, "quiet": self.quiet_ms, "clean": self.clean_ms}


def clamp_level(level: int, floor: int = LEVEL_MIN) -> int:
    return max(max(LEVEL_MIN, floor), min(LEVEL_MAX, int(level)))


def profile(level: int) -> Profile:
    """The tails for ``level``, clamped into the table (so an out-of-range level can never build a shorter tail than L-3)."""
    return Profile(*PROFILES[clamp_level(level)])


# ── state ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class State:
    """One subject's numbers. No words, no audio, no name: a level, counters and times (epoch seconds)."""
    level: int = DEFAULT_LEVEL
    floor: int = LEVEL_MIN
    default: int = DEFAULT_LEVEL
    run: int = 0                      # consecutive clean eligible turns
    ring: str = ""                    # the last <= WINDOW eligible turns, one digit each: half-events of cut (0, 1 or 2)
    after_lengthen: bool = False      # the next shortening needs RUN_AFTER_LENGTHEN
    bad: tuple = ()                   # ((level, at), ...): levels that triggered a lengthen, kept BAD_LEVEL_DAYS
    last_step_at: Optional[float] = None
    pin: str = ""                     # "" | "longer" | "quicker"
    pin_until: float = 0.0
    quicker_guard: int = 0            # eligible turns left in which a cut undoes "quicker"
    eligible: int = 0
    events_half: int = 0
    last_heard_at: Optional[float] = None


def new_state(*, child: bool = False, level: Optional[int] = None) -> State:
    """A fresh subject: L0, or for a child CHILD_DEFAULT with the floor at CHILD_FLOOR. ``level`` seeds a new member from the
    panel's household level (never below the subject's own floor or default for a child)."""
    floor = CHILD_FLOOR if child else LEVEL_MIN
    default = CHILD_DEFAULT if child else DEFAULT_LEVEL
    start = default if level is None else (max(default, int(level)) if child else int(level))
    return State(level=clamp_level(start, floor), floor=floor, default=default)


def _pinned(s: State, now: float) -> str:
    return s.pin if s.pin and now < s.pin_until else ""


def effective_level(s: State, now: float) -> int:
    """The level in force at ``now``. Only a level SHORTER than the subject's default drifts (back up toward it, one step per
    DECAY_EVERY_DAYS once DECAY_IDLE_DAYS have passed unheard): drifting a longer level down would be shortening on no
    evidence. A spoken pin stops the drift. Pure: nothing is stored until an eligible turn commits it."""
    lvl = clamp_level(s.level, s.floor)
    if s.last_heard_at is None or _pinned(s, now) or lvl >= s.default:
        return lvl
    idle = now - s.last_heard_at
    if idle <= DECAY_IDLE_DAYS * DAY_S:
        return lvl
    steps = 1 + int((idle - DECAY_IDLE_DAYS * DAY_S) // (DECAY_EVERY_DAYS * DAY_S))
    return clamp_level(min(s.default, lvl + steps), s.floor)


# ── one turn's measurements ─────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Outcome:
    """What the daemon measured on one turn (integers and fixed enums only, the FIRST_SOUND fields)."""
    rule: str                          # the close rule
    win: str                           # WINDOWS
    still_talking: bool
    talk_chunks: int                   # -1 = no monitor listened
    obs_ms: int                        # the post-close window actually observed
    muted_ms: int                      # -1 = unknown
    frozen: str = ""                   # "" or FREEZES: no evidence counted
    reflex: bool = False               # a reflex sound played at this close
    held: bool = False                 # the server's trailing-"and" hold merged a continuation into this turn
    speech_ms: Optional[int] = None    # None = not measured (the S0 line has no speech length)
    level_used: Optional[int] = None   # the level whose tails closed this recording (None = not stated)
    pause_max_ms: Optional[int] = None  # the longest quiet run INSIDE the turn, speech end to resume (None = not measured)


_COUNT_RANGES = {"talk_chunks": (-1, 10000), "obs_ms": (0, 60000), "muted_ms": (-1, 600000)}
_OPT_RANGES = {"speech_ms": (0, 600000), "level_used": (LEVEL_MIN, LEVEL_MAX), "pause_max_ms": (0, 600000)}


def _is_count(v, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def valid_outcome(o) -> bool:
    """THE validation, shared by the parser and the controller (``classify`` / ``step``), so a hand-built ``Outcome`` gets
    exactly the checks a parsed one does: the enums, every count an int (never a bool) inside its range, the flags real bools,
    and ``still_talking`` agreeing with the daemon's own definition (>= ``STILL_TALKING_CHUNKS`` chunks)."""
    try:
        if not isinstance(o, Outcome) or not isinstance(o.rule, str) or o.win not in WINDOWS:
            return False
        if not isinstance(o.frozen, str) or (o.frozen and o.frozen not in FREEZES):
            return False
        if not all(isinstance(b, bool) for b in (o.still_talking, o.reflex, o.held)):
            return False
        if not all(_is_count(getattr(o, k), lo, hi) for k, (lo, hi) in _COUNT_RANGES.items()):
            return False
        if not all(getattr(o, k) is None or _is_count(getattr(o, k), lo, hi) for k, (lo, hi) in _OPT_RANGES.items()):
            return False
        return (o.talk_chunks >= STILL_TALKING_CHUNKS) == o.still_talking
    except Exception:  # noqa: BLE001 - anything odd is malformed
        return False


_TRUE = ("1", "true", "yes")
_FALSE = ("0", "false", "no", "")


def _int(v, *, lo: int, hi: int) -> int:
    if isinstance(v, bool):
        raise ValueError("bool is not a count")
    if isinstance(v, float):
        if not math.isfinite(v) or v != int(v):
            raise ValueError("not an integer")
        v = int(v)
    elif isinstance(v, str):
        v = int(v.strip())
    elif not isinstance(v, int):
        raise ValueError("not a number")
    if not lo <= v <= hi:
        raise ValueError("out of range")
    return v


def _flag01(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in _TRUE + _FALSE:
        return v.strip().lower() in _TRUE
    raise ValueError("not a 0/1 flag")


def outcome_from_fields(m: Mapping) -> Optional[Outcome]:
    """Validate one turn's fields (a parsed FIRST_SOUND line, or the outcome POST body). ``None`` on ANY malformed or missing
    required field, so the caller's state stays unchanged. ``close`` may be ``deep=640ms`` (the log form) or ``rule``."""
    try:
        rule = m.get("rule")
        if rule is None:
            close = str(m["close"])
            rule = close.split("=", 1)[0]
        rule = str(rule).strip()
        win = str(m["win"]).strip()
        if win not in WINDOWS:
            return None
        frozen = str(m.get("frozen", "") or "").strip()
        if frozen == "-":
            frozen = ""
        if frozen and frozen not in FREEZES:
            return None
        lvl = m.get("level_used")
        sp = m.get("speech_ms")
        pm = m.get("pause_max_ms")
        reflex = m.get("reflex", False)
        if isinstance(reflex, str) and reflex.strip() in ("none", "-"):
            reflex = False
        elif isinstance(reflex, str) and reflex.strip() == "heard":
            reflex = True
        o = Outcome(
            rule=rule, win=win,
            still_talking=_flag01(m["still_talking"]),
            talk_chunks=_int(m["talk_chunks"], lo=-1, hi=10000),
            obs_ms=_int(m["obs_ms"], lo=0, hi=60000),
            muted_ms=_int(m["muted_ms"], lo=-1, hi=600000),
            frozen=frozen,
            reflex=_flag01(reflex),
            held=_flag01(m.get("held", False)),
            speech_ms=None if sp in (None, "", "-1", -1) else _int(sp, lo=0, hi=600000),
            level_used=None if lvl in (None, "", "-") else _int(lvl, lo=LEVEL_MIN, hi=LEVEL_MAX),
            pause_max_ms=None if pm in (None, "", "-", "-1", -1) else _int(pm, lo=0, hi=600000),
        )
        return o if valid_outcome(o) else None
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def classify(o: Optional[Outcome]) -> "tuple[str, int]":
    """(verdict, half-events). Verdict is ``cut``, ``clean`` or ``skip:<reason>``; only cut and clean are evidence.

    A cut needs no full observation window (people talking after the close is positive evidence however short the look). A
    clean verdict needs every unknown resolved: a monitor that listened, the window observed >= MIN_OBS_MS, the muted ms
    known and zero. Anything less is a skip, never "clean". A "clean" verdict is only "nobody talked after THIS close"; what
    it proves about a shorter level is ``proves_clean_at``'s question."""
    if o is None or not valid_outcome(o):
        return "skip:malformed", 0
    if o.frozen:
        return "skip:frozen", 0
    if o.rule not in CLOSE_RULES:
        return "skip:rule", 0
    if o.win == "barge":
        return "skip:barge", 0
    if o.speech_ms is not None and o.speech_ms < MIN_SPEECH_MS:
        return "skip:short", 0
    if o.muted_ms != 0:
        return "skip:muted", 0          # digital silence in the recording (or unknown): the tail did not see the person
    if o.talk_chunks < 0:
        return "skip:unobserved", 0
    if o.still_talking:
        return "cut", HALF
    if o.held:
        return "cut", 1
    if o.obs_ms < MIN_OBS_MS:
        return "skip:short_window", 0
    return "clean", 0


def tolerance_ms(level: int) -> int:
    """The shortest quiet run, counted from a speech end, that can close a recording at ``level``: its clean gate where it has
    one, else its deep tail. (The deep tail really starts after the decay chunks, so counting it from the speech end is the
    cautious side: it can only call a turn unproven that was in fact safe.)"""
    p = profile(level)
    return min(p.deep_ms, p.clean_ms) if p.clean_ms > 0 else p.deep_ms


def proves_clean_at(o: Outcome, level: int) -> bool:
    """Does this CLEAN turn prove a close at ``level`` would have been clean too? Fields used: ``level_used`` and
    ``pause_max_ms``. It needs the level it was observed at, known and >= ``level`` (a longer close's final silence and
    post-close window contain the shorter close's). Unless it was observed AT ``level``, it also needs proof that no quiet run
    INSIDE the turn could have closed a ``level`` recording early (a 600 ms pause, a resume, a finish: L-3's 560 ms gate would
    have cut it): ``pause_max_ms`` known and < ``tolerance_ms(level)``. Anything unknown is False: not evidence."""
    if o.level_used is None or o.level_used < level:
        return False
    if o.level_used == level:
        return True
    return o.pause_max_ms is not None and o.pause_max_ms < tolerance_ms(level)


# ── the controller ──────────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Change:
    reason: str                        # what happened: lengthen_events | lengthen_reflex | undo_quicker | shorten_run | none | skip:* | error
    before: int
    after: int


def _run_needed(s: State, target: int, now: float, reflex_on: bool) -> int:
    need = RUN_AFTER_LENGTHEN if s.after_lengthen else RUN_SHORTEN
    if any(lvl == target and now - at < BAD_LEVEL_DAYS * DAY_S for lvl, at in s.bad):
        need = max(need, RUN_RETURN_TO_BAD)
    return need * (RUN_REFLEX_FACTOR if reflex_on else 1)


def _keep_bad(bad: tuple, now: float) -> tuple:
    return tuple((lvl, at) for lvl, at in bad if now - at < BAD_LEVEL_DAYS * DAY_S)


def _lengthen(s: State, by: int, now: float) -> State:
    to = clamp_level(s.level + by, s.floor)
    if to == s.level:
        return s
    return replace(s, level=to, ring="", run=0, after_lengthen=True, last_step_at=now,
                   bad=_keep_bad(s.bad, now) + ((s.level, now),))


def step(s: State, o: Optional[Outcome], now: float, *, reflex_on: bool = False) -> "tuple[State, Change]":
    """Fold one turn into the state. Pure. A skipped turn returns ``s`` itself (byte-identical), whatever it carried."""
    try:
        verdict, half = classify(o)
        if not math.isfinite(now):
            return s, Change("skip:clock", s.level, s.level)
        before = effective_level(s, now)
        if verdict.startswith("skip"):
            return s, Change(verdict, before, before)
        if verdict == "clean":
            # A clean turn is evidence only for what it PROVES (proves_clean_at); everything else is neutral. A cut needs no
            # such proof: lengthening is the safe direction.
            if o.level_used is None:
                return s, Change("skip:level_unknown", before, before)
            if o.level_used < before:
                return s, Change("skip:level_mismatch", before, before)     # observed shorter: says nothing about this level
            if not proves_clean_at(o, before):
                return s, Change("skip:unproven", before, before)          # a pause inside it would have closed this level
        s = replace(s, level=before, eligible=s.eligible + 1, last_heard_at=now)
        if verdict == "cut":
            ring = (s.ring + str(half))[-WINDOW:]
            s = replace(s, ring=ring, run=0, events_half=s.events_half + half)
            if s.pin == "quicker" and s.quicker_guard > 0:
                s = replace(s, level=clamp_level(s.level + 1, s.floor), pin="", pin_until=0.0, quicker_guard=0, ring="",
                            after_lengthen=True, last_step_at=now)
                return s, Change("undo_quicker", before, s.level)
            if o.reflex and o.still_talking:
                s2 = _lengthen(s, 1, now)
                return s2, Change("lengthen_reflex" if s2.level != before else "none", before, s2.level)
            if sum(int(c) for c in ring) >= EVENTS_TO_LENGTHEN * HALF:
                s2 = _lengthen(s, 1, now)
                return s2, Change("lengthen_events" if s2.level != before else "none", before, s2.level)
            return s, Change("none", before, s.level)
        # clean (proven at this level): it ages the cut window; it counts toward the run only if it ALSO proves the target clean
        target = s.level - 1
        proven = target >= max(LEVEL_MIN, s.floor) and proves_clean_at(o, target)
        s = replace(s, ring=(s.ring + "0")[-WINDOW:], run=s.run + 1 if proven else s.run,
                    quicker_guard=max(0, s.quicker_guard - 1))
        if (proven and _pinned(s, now) != "longer"
                and (s.last_step_at is None or now - s.last_step_at >= STEP_GAP_S)
                and s.run >= _run_needed(s, target, now, reflex_on)):
            s = replace(s, level=target, run=0, after_lengthen=False, last_step_at=now, bad=_keep_bad(s.bad, now))
            return s, Change("shorten_run", before, target)
        return s, Change("none", before, s.level)
    except Exception:  # noqa: BLE001 - the controller never raises and never moves on an error
        lvl = s.level if isinstance(s, State) else DEFAULT_LEVEL
        return s, Change("error", lvl, lvl)


def override(s: State, kind: str, now: float, *, days: float = PIN_DAYS) -> "tuple[State, Change]":
    """A spoken setting (the phrase is parsed elsewhere; this is only the effect). Bypasses the one-step-a-day rule.

    * ``longer`` ("give me more time", "you keep cutting me off"): two levels longer, counted as two cut events, pinned for
      ``days`` against any automatic shortening;
    * ``quicker`` ("you can be quicker with me"): one level shorter, never below the floor; a cut inside the next
      QUICKER_GUARD_TURNS eligible turns undoes it silently; it does not stop lengthening;
    * ``normal`` ("go back to normal listening"): the subject's default level, counters and pin cleared.
    An unknown kind, or a non-finite clock, changes nothing."""
    try:
        if kind not in OVERRIDES or not math.isfinite(now) or not math.isfinite(days) or days <= 0:
            return s, Change("skip:override", s.level, s.level)
        before = effective_level(s, now)
        if kind == "longer":
            to = clamp_level(before + 2, s.floor)
            s2 = replace(s, level=to, ring="", run=0, after_lengthen=True, last_step_at=now, pin="longer",
                         pin_until=now + days * DAY_S, quicker_guard=0, events_half=s.events_half + 2 * HALF,
                         bad=_keep_bad(s.bad, now) + (((before, now),) if to != before else ()))
            return s2, Change("pin_longer", before, to)
        if kind == "quicker":
            to = clamp_level(before - 1, s.floor)
            s2 = replace(s, level=to, run=0, after_lengthen=False, last_step_at=now, pin="quicker" if to != before else "",
                         pin_until=now + days * DAY_S if to != before else 0.0,
                         quicker_guard=QUICKER_GUARD_TURNS if to != before else 0)
            return s2, Change("pin_quicker" if to != before else "none", before, to)
        s2 = replace(s, level=clamp_level(s.default, s.floor), ring="", run=0, after_lengthen=False, pin="", pin_until=0.0,
                     quicker_guard=0, last_step_at=now)
        return s2, Change("reset", before, s2.level)
    except Exception:  # noqa: BLE001
        return s, Change("error", s.level, s.level)


def momentary(level: int) -> Profile:
    """"Give me a second to think": two levels longer for THIS turn only. Nothing is stored (guests get only this)."""
    return profile(clamp_level(level + 2))


# ── who: (panel, member) or (panel, household) ──────────────────────────────────────────────────────────────────────────────
HOUSEHOLD = "*"


def _ok_id(x: str) -> bool:
    return bool(x) and len(x) <= 128 and "|" not in x and x.isprintable() and x.strip() == x


def subject_key(panel_id: str, member: Optional[str] = None) -> Optional[str]:
    """``"<panel>|<member>"`` or ``"<panel>|*"`` (household); ``None`` for an unusable panel id. A guest or unknown voice
    never has a member key: the caller passes a member only when the server verified it."""
    if not isinstance(panel_id, str) or not _ok_id(panel_id):
        return None
    if member is None or member == "":
        return f"{panel_id}|{HOUSEHOLD}"
    if not isinstance(member, str) or not _ok_id(member) or member == HOUSEHOLD or member.lower().startswith("guest"):
        return f"{panel_id}|{HOUSEHOLD}"
    return f"{panel_id}|{member}"


def level_for(states: Mapping[str, State], panel_id: str, member: Optional[str], now: float, *,
              child: bool = False) -> "tuple[int, str, str]":
    """(level, subject key, source) for the NEXT recording on ``panel_id``. Source is ``member`` (the verified member's own
    row), ``household`` (the panel's household row: the speaker is unknown, or a member with no row yet) or ``default``.
    A verified member with no row starts at the household level (a child at least CHILD_DEFAULT). An unusable panel id is
    the default level with an empty key, so nothing can be recorded against it."""
    hkey = subject_key(panel_id, None)
    if hkey is None:
        return (CHILD_DEFAULT if child else DEFAULT_LEVEL), "", "default"
    mkey = subject_key(panel_id, member)
    if mkey != hkey and mkey in states:
        lvl = effective_level(states[mkey], now)
        return (max(CHILD_FLOOR, lvl) if child else lvl), mkey, "member"   # the CURRENT role's floor, whatever the row says
    if hkey in states:
        lvl = effective_level(states[hkey], now)
        if mkey != hkey:
            return new_state(child=child, level=lvl).level, mkey, "household"
        return lvl, hkey, "household"
    if mkey != hkey:
        return new_state(child=child).level, mkey, "default"
    return DEFAULT_LEVEL, hkey, "default"
