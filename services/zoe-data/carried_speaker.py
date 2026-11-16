"""The carried verified speaker: who was the last CONFIDENTLY verified speaker of a panel's conversation window, for a short ttl.

Listen pace must choose the next recording's end-of-turn wait BEFORE that recording starts, so it can never use the next turn's
own voice score. Inside a follow-up or conversation window the next speaker is almost always the one just verified, so the
server's verdict on THIS turn is carried to the next one (design: ``docs/knowledge/voice-listen-pace.md``, "Who"; the reflex's
S4 window turns want the same fact as a second check).

What it is NOT: an identity, an auth input, or anything a guest's turn can read. It is a hint with a ttl:

* written only where the voice path already holds a server-verified member (``note_verified``); a guest or unknown voice clears
  it (``note_unverified``);
* invalidated by expiry (the ttl, capped at the voice-session ttl, ``voice_session.ttl_s()``), by any wake (a new wake can be
  anyone: ``on_wake``), by a session close (``on_session_close``), by forget (``forget_user``) and by a restart (memory only);
* sent DOWN to the panel only as a boolean and a remaining ttl (``frame_field``): the panel never learns who.

In memory only (no file), bounded to ``MAX_PANELS``, monotonic clock, never raises. Not wired yet: see the doc's wiring note.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Optional

MAX_PANELS = 64
DEFAULT_TTL_S = 30          # a follow-up / conversation window, not a session
_FALLBACK_SESSION_TTL_S = 120

_lock = threading.Lock()
_mem: "dict[str, list]" = {}     # panel -> [member, expires_at, last_seen] (monotonic seconds)
_clock = time.monotonic                        # monkeypatchable


def _session_ttl() -> int:
    try:
        import voice_session  # lazy: keeps this module import-light

        return int(voice_session.ttl_s())
    except Exception:  # noqa: BLE001
        return _FALLBACK_SESSION_TTL_S


def _nobody(member: Optional[str]) -> bool:
    m = (member or "").strip()
    if not m:
        return True
    try:
        import user_filters

        return bool(user_filters.is_unidentified(m))
    except Exception:  # noqa: BLE001 - without the repo predicate, only the plain guest shapes
        return m.lower() in ("guest", "unknown") or m.lower().startswith("guest")


def note_verified(panel_id: str, member: str, *, ttl_s: float = DEFAULT_TTL_S, now: Optional[float] = None) -> bool:
    """The server verified ``member`` on ``panel_id``'s turn that just ended. True when carried."""
    try:
        if not panel_id or _nobody(member) or not math.isfinite(float(ttl_s)) or ttl_s <= 0:
            note_unverified(panel_id)
            return False
        t = _clock() if now is None else float(now)
        ttl = min(float(ttl_s), float(_session_ttl()))
        with _lock:
            _mem.pop(panel_id, None)
            _mem[panel_id] = [member.strip(), t + ttl, t]   # member, this entry's own expiry, the last time seen
            while len(_mem) > MAX_PANELS:
                _mem.pop(next(iter(_mem)))
        return True
    except Exception:  # noqa: BLE001
        return False


def note_unverified(panel_id: str) -> None:
    """This turn's voice was not confirmed (a guest, an unknown voice, under the bar): nothing is carried."""
    with _lock:
        _mem.pop(panel_id, None)


on_wake = note_unverified           # any wake: the next speaker is unknown until proven
on_session_close = note_unverified  # a lock, an unverified voice, a different voice


def current(panel_id: str, *, now: Optional[float] = None) -> Optional[str]:
    """The carried member for ``panel_id``'s next window turn, or None (none, expired, or a clock that ran backwards)."""
    try:
        t = _clock() if now is None else float(now)
        with _lock:
            ent = _mem.get(panel_id)
            if ent is None:
                return None
            # ANY backward step of the clock ends the carry: against the time the entry was recorded and against every later
            # read (comparing the time left with the session's TTL missed a small step on an entry shorter than the session:
            # recorded at 1000 for 30 s, read at 999, it still answered with 31 s left)
            if not t < ent[1] or t < ent[2]:
                _mem.pop(panel_id, None)
                return None
            ent[2] = t
            return ent[0]
    except Exception:  # noqa: BLE001
        return None


def frame_field(panel_id: str, *, now: Optional[float] = None) -> dict:
    """The done frame's ``speaker`` key: ``{"v": 1, "verified": bool, "ttl_s": int}``. A boolean, never a name."""
    t = _clock() if now is None else float(now)
    m = current(panel_id, now=t)
    if m is None:
        return {"v": 1, "verified": False, "ttl_s": 0}
    with _lock:
        ent = _mem.get(panel_id)
    left = int(max(0.0, (ent[1] - t))) if ent else 0
    return {"v": 1, "verified": left > 0, "ttl_s": left}


def forget_user(member_id: str) -> int:
    """Drop ``member_id`` from every panel. Returns entries removed. Never raises."""
    try:
        m = (member_id or "").strip()
        with _lock:
            gone = [k for k, ent in _mem.items() if ent[0] == m]
            for k in gone:
                _mem.pop(k, None)
        return len(gone)
    except Exception:  # noqa: BLE001
        return 0


def reset() -> None:
    with _lock:
        _mem.clear()
