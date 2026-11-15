"""Presence fusion: who is home, who is in which room, who just left, arrived or woke (flag ``ZOE_PRESENCE_FUSION=off|shadow``).

PURE and in memory. Nothing here reads a sensor, a database, a file or the network, and no live path calls it yet: producers
hand it typed ``Observation`` records, consumers ask it questions with an explicit ``now``. Design and the signals table:
``docs/knowledge/presence-fusion.md``; the lab that measures it: ``labs/presence/``.

The model (the log-odds-in-bits scale of ``docs/architecture/identity-presence-engine.md`` section 2.3, applied to the house):

* Per person, ``L_home`` = the sum over evidence GROUPS of the capped, decayed bits of that person's newest observation of each
  signal kind. Positive bits support "home", negative bits support "away". ``home`` at ``L >= HOME_BITS``, ``away`` at
  ``L <= -AWAY_BITS``, otherwise ``unknown``. Confidence = ``2^|L| / (1 + 2^|L|)``.
* Every contribution decays toward ZERO bits after its hold window, so stale evidence becomes ``unknown`` - never ``away``.
  Absence of evidence is not evidence of leaving: only a signal that positively reports "away" (a phone off the home network,
  a BLE beacon unseen by every scanner) can move a person to ``away``.
* A level reading that CHANGES to away (``since`` = the change time) breaks older home evidence: that evidence decays from
  the change with ``BREAK_HALF_LIFE_S`` and no hold. Home evidence never breaks an away reading; it only adds, so one voice
  (or one TV voice falsely accepted) cannot outweigh a phone that says it left.
* Room: the newest room-capable observation of the person wins while its decayed bits are at least ``ROOM_BITS``; a person
  believed away is in no room.
* Events (``step`` / ``observe`` return them): ``left`` and ``arrived`` with hysteresis (the new state must hold for a dwell;
  a door event near the change shortens it; ``left`` also needs household activity shortly before and the person's own
  activity within a few hours, because a sleeping house - or a sleeping person - does not leave), ``woke`` (first activity after a long quiet window inside the morning band), ``room_change``.
* People: only registered members are tracked. A ``guest`` is never registered, so a guest is only ever "someone" room
  occupancy. A member may switch tracking off (then always unknown, observations dropped and erased). A child's tracking and
  strictness are set by a guardian, and a child's strictness cannot go below ``balanced``.
* Privacy: everything is per person and visible only to that person. Another member sees a person's HOME state (never the
  room, never the evidence) only under a declared ``HouseholdRule``. ``others_present`` answers a room question without names.
* ``forget_user`` erases a person's observations, settings, tracker and events (``forget_reach.py`` entry
  ``presence_fusion_memory``; called from ``MemoryService.delete_user``).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

HOME, AWAY, UNKNOWN = "home", "away", "unknown"
HERE, NOT_HERE = "here", "not_here"
STRICTNESS = {"relaxed": 0.80, "balanced": 0.90, "strict": 0.97}  # confidence a privacy-relevant use needs
ROLES = ("adult", "child")
HOME_BITS = 3.0
AWAY_BITS = 3.0
ROOM_BITS = 3.0
BREAK_HALF_LIFE_S = 120.0
DWELL_LEFT_S = 600.0
DWELL_LEFT_DOOR_S = 60.0
DWELL_ARRIVE_S = 180.0
DWELL_ARRIVE_DOOR_S = 0.0
DOOR_WINDOW_S = (600.0, 120.0)        # a door event this long before / after a change corroborates it
LEFT_CONTEXT_S = 1800.0               # household activity needed this long before a "left" (0 = rule off)
LEFT_OWN_ACTIVITY_S = 3 * 3600.0      # ... and the person's OWN activity this long before it (0 = rule off)
QUIET_S = 4 * 3600.0                  # "woke" = first activity after at least this long without any
MORNING_BAND = (4.5, 11.0)            # local hours
SOMEONE_ROOM_S = 600.0                # how long "someone" evidence keeps a room occupied for others_present


@dataclass(frozen=True)
class SignalSpec:
    kind: str
    group: str             # caps apply per group: two readings of one phone are one fact
    home_bits: float       # bits for value=home
    away_bits: float       # bits for value=away (<= 0); 0 = this signal cannot say "away"
    hold_s: float          # full strength this long, then halves every half_life_s
    half_life_s: float
    room_bits: float = 0.0  # bits for "in this room"; 0 = carries no room
    room_hold_s: float = 120.0
    room_half_life_s: float = 180.0
    identifies: str = "person"   # person | someone
    activity: bool = False       # counts as activity (woke, the leaving context)
    level: bool = False          # a polled state reading (re-asserted each poll) rather than an event
    others_s: float = 0.0        # "someone" evidence of a person OTHER than the member, kept this long (0 = says nothing)
    hardware: bool = False       # needs hardware the house does not have


# Starting values [proposal]: to be replaced by shadow data (the lab's numbers are only as good as these).
SIGNALS: dict[str, SignalSpec] = {s.kind: s for s in (
    SignalSpec("voice_verified", "VOICE", 6.0, 0.0, 900, 1800, room_bits=6.0, activity=True),
    SignalSpec("pin_unlock", "KNOW", 8.0, 0.0, 900, 1800, room_bits=7.0, room_hold_s=300, room_half_life_s=300, activity=True),
    SignalSpec("panel_touch_member", "KNOW", 4.0, 0.0, 600, 1200, room_bits=5.0, activity=True),
    SignalSpec("alarm_dismissed", "CONTEXT", 3.0, 0.0, 1800, 3600, room_bits=3.0, activity=True),
    SignalSpec("pendant_sync", "WEARABLE", 2.0, 0.0, 300, 600),
    SignalSpec("member_chat", "CONTEXT", 0.0, 0.0, 0, 1, activity=True),     # alive, place unknown
    SignalSpec("phone_tracker", "DEVICE", 3.0, -4.0, 300, 600, level=True),   # HA person / device_tracker
    SignalSpec("someone_voice", "SOMEONE", 0.0, 0.0, 0, 1, room_bits=4.0, identifies="someone", activity=True,
               others_s=SOMEONE_ROOM_S),     # a voice that did not verify: maybe another person, maybe the member's own miss
    SignalSpec("room_toggle", "SOMEONE", 0.0, 0.0, 0, 1, room_bits=3.0, identifies="someone", activity=True),
    # hardware adapters: declared interface, no producer exists
    SignalSpec("face_match", "FACE", 5.0, 0.0, 900, 1800, room_bits=6.0, activity=True, hardware=True),
    SignalSpec("ble_irk", "DEVICE", 3.0, -4.0, 120, 300, room_bits=4.0, room_hold_s=60, room_half_life_s=120, level=True,
               hardware=True),
    SignalSpec("camera_person", "SOMEONE", 0.0, 0.0, 0, 1, room_bits=4.0, room_hold_s=30, room_half_life_s=60,
               identifies="someone", activity=True, hardware=True, others_s=60.0),   # others only when count >= 2
    SignalSpec("mmwave", "SOMEONE", 0.0, 0.0, 0, 1, room_bits=4.0, room_hold_s=30, room_half_life_s=60, identifies="someone",
               level=True, hardware=True),    # occupancy, not activity: it sees a sleeping person; it cannot count
    SignalSpec("door_contact", "ENTRY", 0.0, 0.0, 0, 1, identifies="someone", hardware=True),
)}
GROUP_CAPS = {"VOICE": (0.0, 7.0), "KNOW": (0.0, 8.0), "CONTEXT": (0.0, 3.0), "WEARABLE": (0.0, 2.0),
              "DEVICE": (-5.0, 3.0), "FACE": (0.0, 5.0)}


@dataclass(frozen=True)
class Observation:
    t: float                       # epoch seconds
    kind: str                      # a SIGNALS key
    person: Optional[str] = None   # member id; None = someone
    value: str = HOME              # home | away (away only for a signal with away_bits)
    room: Optional[str] = None
    count: int = 1                 # people seen, for a counting sensor (camera person-detect)


@dataclass(frozen=True)
class Belief:
    state: str
    confidence: float
    age_s: Optional[float]
    evidence: tuple                # ((kind, bits, age_s), ...) strongest first


@dataclass(frozen=True)
class Event:
    kind: str                      # left | arrived | woke | room_change
    person: str
    t: float                       # when it was decided
    since: float                   # when the evidence for it began
    confidence: float
    evidence: tuple                # evidence classes (signal kinds)
    room: Optional[str] = None


@dataclass
class PersonSettings:
    role: str = "adult"
    tracking: bool = True
    strictness: str = "balanced"
    guardians: tuple = ()


@dataclass(frozen=True)
class HouseholdRule:
    """A declared household rule. ``share_home``: ``subject`` lets every member see their home/away (their own opt-in).
    ``guardian_home``: a child's home/away is visible to the child's guardians."""
    kind: str                      # share_home | guardian_home
    subject: str


@dataclass
class _Reading:
    obs: Observation
    since: float                   # first time this value was seen (level readings); == obs.t for events


@dataclass
class _Tracker:
    confirmed: Optional[str] = None   # last decided home/away
    pending: Optional[str] = None
    pending_since: float = 0.0
    room: Optional[str] = None
    last_activity: Optional[float] = None
    woke_day: Optional[str] = None


def _decayed(bits: float, age: float, hold: float, half_life: float) -> float:
    if age <= hold:
        return bits
    return bits * 0.5 ** ((age - hold) / max(half_life, 1e-9))


def _conf(bits: float) -> float:
    b = min(abs(bits), 60.0)
    return 2.0 ** b / (1.0 + 2.0 ** b)


def _visible(viewer: str, subject: str, settings: Optional[PersonSettings], rules: Iterable[HouseholdRule]) -> bool:
    """May ``viewer`` see ``subject``'s HOME state? Self always; anyone else only under a declared rule."""
    if viewer == subject:
        return True
    for r in rules:
        if r.subject != subject:
            continue
        if r.kind == "share_home":
            return True
        if r.kind == "guardian_home" and settings is not None and settings.role == "child" and viewer in settings.guardians:
            return True
    return False


class PresenceFusion:
    def __init__(self, *, tz_offset_s: float = 0.0, left_context_s: float = LEFT_CONTEXT_S, dwell_left_s: float = DWELL_LEFT_S,
                 left_own_activity_s: float = LEFT_OWN_ACTIVITY_S, start: Optional[float] = None):
        self.tz_offset_s = tz_offset_s
        self.left_context_s = left_context_s
        self.left_own_activity_s = left_own_activity_s
        self.dwell_left_s = dwell_left_s
        self._start = start
        self._lock = threading.RLock()
        self._settings: dict[str, PersonSettings] = {}
        self._home: dict[str, dict[str, _Reading]] = {}       # person -> kind -> newest reading
        self._room: dict[str, Optional[_Reading]] = {}        # person -> newest room-capable observation
        self._others: dict[str, float] = {}                   # room -> until when "someone other than the member" holds
        self._doors: list[float] = []
        self._house_activity: Optional[float] = None
        self._trackers: dict[str, _Tracker] = {}
        self._events: dict[str, list[Event]] = {}

    # -- people and settings --------------------------------------------------------------------------------------------------
    def register(self, person: str, *, role: str = "adult", guardians: Iterable[str] = ()) -> None:
        if role not in ROLES:
            raise ValueError(f"role {role!r} is not tracked (a guest is never a tracked person)")
        with self._lock:
            self._settings[person] = PersonSettings(role=role, guardians=tuple(guardians))
            self._trackers.setdefault(person, _Tracker())

    def _may_set(self, person: str, actor: str) -> PersonSettings:
        s = self._settings.get(person)
        if s is None:
            raise KeyError(person)
        allowed = actor in s.guardians if s.role == "child" else actor == person
        if not allowed:
            raise PermissionError("only the person (a guardian for a child) changes their presence settings")
        return s

    def set_tracking(self, person: str, enabled: bool, *, actor: str) -> None:
        with self._lock:
            s = self._may_set(person, actor)
            s.tracking = bool(enabled)
            if not enabled:
                self._erase_evidence(person)

    def set_strictness(self, person: str, level: str, *, actor: str) -> None:
        if level not in STRICTNESS:
            raise ValueError(level)
        with self._lock:
            s = self._may_set(person, actor)
            if s.role == "child" and STRICTNESS[level] < STRICTNESS["balanced"]:
                raise ValueError("a child's strictness cannot go below balanced")
            s.strictness = level

    def _tracked(self, person: Optional[str]) -> bool:
        s = self._settings.get(person or "")
        return s is not None and s.tracking

    # -- input ----------------------------------------------------------------------------------------------------------------
    def observe(self, obs: Observation) -> list[Event]:
        spec = SIGNALS.get(obs.kind)
        if spec is None:
            return []
        out: list[Event] = []
        with self._lock:
            if self._start is None:
                self._start = obs.t
            if spec.activity:
                self._house_activity = max(self._house_activity or obs.t, obs.t)
            if spec.identifies == "someone" or obs.person is None:
                if obs.kind == "door_contact":
                    self._doors = [d for d in self._doors if obs.t - d < 2 * DOOR_WINDOW_S[0]] + [obs.t]
                elif obs.room and spec.others_s > 0 and (obs.kind != "camera_person" or obs.count >= 2):
                    self._others[obs.room] = max(self._others.get(obs.room, 0.0), obs.t + spec.others_s)
                return out
            if not self._tracked(obs.person):
                return out                                  # unregistered (a guest) or tracking off: dropped, not stored
            p = obs.person
            prev = self._home.setdefault(p, {}).get(obs.kind)
            since = prev.since if (spec.level and prev is not None and prev.obs.value == obs.value) else obs.t
            self._home[p][obs.kind] = _Reading(obs, since)
            if spec.room_bits > 0 and obs.room and obs.value == HOME:
                self._room[p] = _Reading(obs, obs.t)
            if spec.activity:
                out.extend(self._maybe_woke(p, obs, spec))
            self._events.setdefault(p, []).extend(out)
        return out

    def _maybe_woke(self, p: str, obs: Observation, spec: SignalSpec) -> list[Event]:
        tr = self._trackers.setdefault(p, _Tracker())
        last = tr.last_activity if tr.last_activity is not None else self._start
        tr.last_activity = obs.t
        local = datetime.fromtimestamp(obs.t + self.tz_offset_s, tz=timezone.utc)
        hour = local.hour + local.minute / 60.0
        day = local.date().isoformat()
        if (last is None or obs.t - last < QUIET_S or not (MORNING_BAND[0] <= hour < MORNING_BAND[1])
                or tr.woke_day == day or self._home_bits(p, obs.t)[0] <= -AWAY_BITS):
            return []
        tr.woke_day = day
        conf = _conf(spec.home_bits) if spec.home_bits else 0.5
        return [Event("woke", p, obs.t, last, round(conf, 3), (obs.kind,))]

    # -- fusion ---------------------------------------------------------------------------------------------------------------
    def _home_bits(self, p: str, now: float) -> tuple[float, list]:
        readings = self._home.get(p, {})
        away_change = max((r.since for r in readings.values() if r.obs.value == AWAY), default=None)
        groups: dict[str, float] = {}
        lines = []
        for kind, r in readings.items():
            spec = SIGNALS[kind]
            age = now - r.obs.t
            if age < 0:
                continue
            if r.obs.value == AWAY:
                raw = spec.away_bits
                bits = _decayed(raw, age, spec.hold_s, spec.half_life_s)
            else:
                raw = spec.home_bits
                if away_change is not None and r.obs.t <= away_change:
                    bits = _decayed(_decayed(raw, age, spec.hold_s, spec.half_life_s), now - away_change, 0.0, BREAK_HALF_LIFE_S)
                else:
                    bits = _decayed(raw, age, spec.hold_s, spec.half_life_s)
            if abs(bits) < 0.05:
                continue
            groups[spec.group] = groups.get(spec.group, 0.0) + bits
            lines.append((kind, round(bits, 2), round(age, 1)))
        total = 0.0
        for g, v in groups.items():
            lo, hi = GROUP_CAPS.get(g, (0.0, 0.0))
            total += min(max(v, lo), hi)
        lines.sort(key=lambda x: -abs(x[1]))
        return total, lines

    def belief(self, person: str, now: float) -> Belief:
        with self._lock:
            if not self._tracked(person):
                return Belief(UNKNOWN, 0.0, None, ())
            bits, lines = self._home_bits(person, now)
        state = HOME if bits >= HOME_BITS else AWAY if bits <= -AWAY_BITS else UNKNOWN
        support = [ln for ln in lines if (ln[1] > 0) == (state == HOME)] if state != UNKNOWN else lines
        age = min((ln[2] for ln in support), default=None)
        return Belief(state, round(_conf(bits), 3) if state != UNKNOWN else 0.0, age, tuple(support))

    def room_of(self, person: str, now: float) -> tuple[Optional[str], float, tuple]:
        with self._lock:
            if not self._tracked(person):
                return None, 0.0, ()
            r = self._room.get(person)
            if r is None or self.belief(person, now).state == AWAY:
                return None, 0.0, ()
            spec = SIGNALS[r.obs.kind]
            bits = _decayed(spec.room_bits, now - r.obs.t, spec.room_hold_s, spec.room_half_life_s)
            if bits < ROOM_BITS:
                return None, 0.0, ()
            return r.obs.room, round(_conf(bits), 3), ((r.obs.kind, round(bits, 2), round(now - r.obs.t, 1)),)

    def room_belief(self, person: str, room: str, now: float) -> Belief:
        where, conf, ev = self.room_of(person, now)
        if where is None:
            return Belief(NOT_HERE if self.belief(person, now).state == AWAY else UNKNOWN, 0.0, None, ())
        return Belief(HERE if where == room else NOT_HERE, conf, ev[0][2] if ev else None, ev)

    def explain(self, person: str, now: float) -> list[dict]:
        """Why Zoe thinks what she thinks about ``person``: the supporting observations, strongest first. For that person only."""
        b = self.belief(person, now)
        return [{"signal": k, "bits": bits, "age_s": age, "supports": HOME if bits > 0 else AWAY} for k, bits, age in b.evidence]

    # -- events ---------------------------------------------------------------------------------------------------------------
    def _door_near(self, t: float) -> bool:
        before, after = DOOR_WINDOW_S
        return any(t - before <= d <= t + after for d in self._doors)

    def step(self, now: float) -> list[Event]:
        """Advance the hysteresis to ``now``; returns the events decided. Call it on a timer (the lab: once a minute)."""
        out: list[Event] = []
        with self._lock:
            for p, s in self._settings.items():
                if not s.tracking:
                    continue
                tr = self._trackers.setdefault(p, _Tracker())
                b = self.belief(p, now)
                if b.state == UNKNOWN or b.state == tr.confirmed:
                    tr.pending = None
                elif tr.confirmed is None:
                    tr.confirmed = b.state                  # first definite state: a baseline, not an event
                else:
                    if tr.pending != b.state:
                        tr.pending, tr.pending_since = b.state, now
                    # a door only vouches for someone who was up and about: a phone dropping off the Wi-Fi while another
                    # member walks out is not this person leaving
                    door = self._door_near(tr.pending_since) and (b.state != AWAY or (
                        tr.last_activity is not None and tr.pending_since - tr.last_activity <= LEFT_CONTEXT_S))
                    if b.state == AWAY:
                        dwell = DWELL_LEFT_DOOR_S if door else self.dwell_left_s
                    else:
                        dwell = DWELL_ARRIVE_DOOR_S if door else DWELL_ARRIVE_S
                    if now - tr.pending_since >= dwell:
                        ok = True
                        if b.state == AWAY and self.left_context_s > 0:
                            act = self._house_activity
                            ok = act is not None and tr.pending_since - act <= self.left_context_s
                        if b.state == AWAY and ok and self.left_own_activity_s > 0:
                            own = tr.last_activity          # a person asleep since last night does not walk out
                            ok = own is not None and tr.pending_since - own <= self.left_own_activity_s
                        tr.pending = None
                        if ok:                              # a suppressed "left" keeps "home", so the return is no "arrived"
                            tr.confirmed = b.state
                            ev = tuple(sorted({k for k, _, _ in b.evidence} | ({"door_contact"} if door else set())))
                            out.append(Event("left" if b.state == AWAY else "arrived", p, now, tr.pending_since or now,
                                             b.confidence, ev))
                where, conf, ev = self.room_of(p, now)
                if where is not None and where != tr.room:
                    if tr.room is not None:
                        out.append(Event("room_change", p, now, now - (ev[0][2] if ev else 0), conf,
                                         tuple(k for k, _, _ in ev), room=where))
                    tr.room = where
                elif where is None and b.state == AWAY:
                    tr.room = None
            for e in out:
                self._events.setdefault(e.person, []).append(e)
                del self._events[e.person][:-200]
        return out

    def events_for(self, viewer: str, subject: str) -> list[Event]:
        """A person's own events. Nobody else's: an event names a room and its evidence, which no household rule shares."""
        with self._lock:
            return list(self._events.get(subject, ())) if viewer == subject else []

    # -- consumers ------------------------------------------------------------------------------------------------------------
    def view(self, viewer: str, subject: str, now: float, rules: Iterable[HouseholdRule] = ()) -> Optional[dict]:
        """What ``viewer`` may know about ``subject``. Self: state, room, confidence, evidence. Another member: the HOME state
        only, and only under a declared rule. Otherwise None (not "unknown": the viewer is told nothing at all)."""
        with self._lock:
            settings = self._settings.get(subject)
            if settings is None or not _visible(viewer, subject, settings, rules):
                return None
            b = self.belief(subject, now)
            if viewer != subject:
                return {"state": b.state}
            room, rconf, _ = self.room_of(subject, now)
            return {"state": b.state, "confidence": b.confidence, "age_s": b.age_s, "room": room, "room_confidence": rconf,
                    "why": self.explain(subject, now)}

    def sure_enough(self, person: str, room: str, now: float) -> bool:
        """Is Zoe sure enough, by ``person``'s own strictness, that they are in ``room`` (a privacy-relevant use)?"""
        s = self._settings.get(person)
        if s is None or not s.tracking:
            return False
        rb = self.room_belief(person, room, now)
        return rb.state == HERE and rb.confidence >= STRICTNESS[s.strictness]

    def others_present(self, room: str, member: str, now: float) -> str:
        """``yes`` when another tracked person is believed in ``room`` or evidence of someone else is fresh there (a voice that
        did not verify as anyone, in the last 10 min; a person-detect counting two or more). Occupancy that cannot count
        (mmWave, a light switched on) says nothing here: it is the member as much as anyone; ``unknown`` in the store's first ``SOMEONE_ROOM_S`` (a consumer
        treats unknown as crowded, like ``day_review_raise.others_present``); else ``none_observed``. Never names anyone."""
        with self._lock:
            if self._start is None or now - self._start < SOMEONE_ROOM_S:
                return "unknown"
            for p in self._settings:
                if p != member and self.room_of(p, now)[0] == room:
                    return "yes"
            return "yes" if self._others.get(room, 0.0) >= now else "none_observed"

    # -- forget ---------------------------------------------------------------------------------------------------------------
    def _erase_evidence(self, person: str) -> int:
        n = len(self._home.pop(person, {})) + (1 if self._room.pop(person, None) else 0) + len(self._events.pop(person, []))
        if person in self._trackers:
            self._trackers[person] = _Tracker()
        return n

    def forget(self, person: str) -> int:
        with self._lock:
            n = self._erase_evidence(person)
            self._trackers.pop(person, None)
            return n + (1 if self._settings.pop(person, None) else 0)

    def holds(self, person: str) -> bool:
        with self._lock:
            return any(person in d for d in (self._settings, self._home, self._room, self._trackers, self._events))


# -- the process store (shadow only) ------------------------------------------------------------------------------------------
_STORE = PresenceFusion()


def mode() -> str:
    from typed_env import flag

    return flag("ZOE_PRESENCE_FUSION")


def record(obs: Observation) -> list[Event]:
    """The one producer entry point. ``off`` (default) stores nothing. ``shadow`` fuses in memory; nothing acts on it."""
    try:
        if mode() != "shadow":
            return []
        return _STORE.observe(obs)
    except Exception:  # noqa: BLE001 - presence is never allowed to break a producer
        return []


def forget_user(member_id: str) -> int:
    """Erase everything the process store holds about ``member_id``, whatever the flag says. Never raises."""
    try:
        return _STORE.forget((member_id or "").strip()) if (member_id or "").strip() else 0
    except Exception:  # noqa: BLE001
        return 0

