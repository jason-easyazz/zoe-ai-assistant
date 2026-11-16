"""The panel half of listen pace: which end-of-turn tails the NEXT recording would be built with (stdlib only, so it imports on
the Pi with nothing from zoe-data). SSOT: ``docs/knowledge/voice-listen-pace.md``.

The server owns the state and the controller (``services/zoe-data/listen_pace.py``). The panel only APPLIES a tiny profile it
was handed in the done frame, keeps the last panel profile it APPLIED over restarts (``~/.zoe-voice/listen_pace.json``, 0600), and
reports one turn's numbers back up. This module decides nothing about levels and touches no device, audio or network:

* ``parse_listen(done_frame)`` validates the optional ``listen`` key; anything malformed is ``None`` (today's tails);
* ``PaceHolder`` keeps the panel profile and the short-lived window profile; ``choose(win, now)`` says which applies to the
  next recording and from where (``env`` = today's globals, byte-identical);
* ``tail_kwargs(profile)`` is the keyword set the ``_Endpointer`` will take once its seam exists (not yet: unwired);
* ``outcome_body(...)`` is the UP POST body (integers and fixed enums only, never text);
* ``pace_line(...)`` is the one log line per turn.

Every profile is clamped to the same floors and ceiling the server table uses, so a malformed or hostile frame can never build
a tail shorter than the fastest level: clean gate >= 560 ms (or off), deep tail >= 640 ms and above the speculative tail, quiet
tail <= 1200 ms.

Env (the daemon's convention for its own modules, like ``REFLEX_*``): ``LISTEN_PACE`` = off (default) | shadow | on;
``LISTEN_PACE_FILE`` (default ``~/.zoe-voice/listen_pace.json``).
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

MODES = ("off", "shadow", "on")
LISTEN_PACE = "off"
LISTEN_PACE_FILE = "~/.zoe-voice/listen_pace.json"

CLEAN_MIN_MS = 560          # the daemon floors the clean gate at 500, which ceil-rounds to 7 chunks = 560
DEEP_MIN_MS = 640           # never under 640 otherwise (480 measured 8.3 % false cuts against 3.6 % at 640)
DEEP_MAX_MS = 960
QUIET_MIN_MS = 800
QUIET_MAX_MS = 1200         # dead air; the hesitation cap is 1500
LEVEL_MIN, LEVEL_MAX = -3, 4
WINDOW_TTL_MAX_S = 120      # the server's voice-session TTL default: a window profile never outlives it
_MAX_FILE_BYTES = 4096

SRCS = ("env", "panel", "window", "cache")
WINS = ("wake", "followup", "conversation", "barge")
RULES = ("deep", "quiet", "clean", "hesitation", "no_speech", "cap", "other", "none")
FALLS = ("clean", "hesitating", "none")
FREEZES = ("media", "night", "noise", "first_wake", "speculation", "cap")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def settings(env: Optional[Mapping[str, str]] = None) -> "tuple[str, str]":
    """(mode, cache path) from ``env`` (default ``os.environ``); an unknown mode is ``off``."""
    env = os.environ if env is None else env
    m = (env.get("LISTEN_PACE") or "").strip().lower() or LISTEN_PACE
    path = (env.get("LISTEN_PACE_FILE") or "").strip() or LISTEN_PACE_FILE
    return (m if m in MODES else "off"), path


@dataclass(frozen=True)
class Profile:
    deep_ms: int
    quiet_ms: int
    clean_ms: int           # 0 = the clean-tail gate is off
    level: Optional[int] = None

    def as_dict(self) -> dict:
        d = {"deep": self.deep_ms, "quiet": self.quiet_ms, "clean": self.clean_ms}
        if self.level is not None:
            d["level"] = self.level
        return d


def _int(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError("not an int")
    return v


def parse_profile(obj, *, speculative_tail_ms: int = 0) -> Optional[Profile]:
    """A profile dict, strictly: integers only, inside the floors and the ceiling, quiet above deep, deep above the speculative
    tail (otherwise B1.1 would go inert). ``None`` on anything else, never a clamp-and-guess."""
    try:
        if not isinstance(obj, Mapping):
            return None
        deep, quiet, clean = _int(obj["deep"]), _int(obj["quiet"]), _int(obj["clean"])
        lvl = obj.get("level")
        lvl = None if lvl is None else _int(lvl)
        if not DEEP_MIN_MS <= deep <= DEEP_MAX_MS or not QUIET_MIN_MS <= quiet <= QUIET_MAX_MS or quiet <= deep:
            return None
        if clean != 0 and not CLEAN_MIN_MS <= clean <= deep + 160:
            return None
        if speculative_tail_ms > 0 and deep <= speculative_tail_ms:
            return None
        if lvl is not None and not LEVEL_MIN <= lvl <= LEVEL_MAX:
            return None
        return Profile(deep, quiet, clean, lvl)
    except (KeyError, ValueError, TypeError):
        return None


@dataclass(frozen=True)
class Listen:
    apply: bool                       # False in server shadow: log what it WOULD use, use today's tails
    panel: Optional[Profile]
    next: Optional[Profile]           # the next window turn's profile (the carried verified speaker's), if any
    ttl_s: int
    token: str


def parse_listen(frame, *, speculative_tail_ms: int = 0) -> Optional[Listen]:
    """The done frame's optional ``listen`` key: ``{"v": 1, "apply": bool, "panel": {...}, "next": {..., "ttl_s": N},
    "token": "<opaque>"}``. ``None`` when absent or malformed (an old server sends nothing: today's tails)."""
    try:
        d = frame.get("listen") if isinstance(frame, Mapping) else None
        if not isinstance(d, Mapping) or d.get("v") != 1:
            return None
        token = d.get("token")
        if not isinstance(token, str) or not TOKEN_RE.match(token):
            return None
        apply = d.get("apply")
        if not isinstance(apply, bool):
            return None
        panel = parse_profile(d.get("panel"), speculative_tail_ms=speculative_tail_ms) if "panel" in d else None
        nxt_raw = d.get("next")
        nxt, ttl = None, 0
        if nxt_raw is not None:
            nxt = parse_profile(nxt_raw, speculative_tail_ms=speculative_tail_ms)
            ttl = nxt_raw.get("ttl_s") if isinstance(nxt_raw, Mapping) else None
            if nxt is None or isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= WINDOW_TTL_MAX_S:
                return None
        if panel is None and nxt is None:
            return None
        return Listen(apply, panel, nxt, ttl, token)
    except Exception:  # noqa: BLE001 - a frame can never raise into the daemon
        return None


def tail_kwargs(p: Optional[Profile]) -> dict:
    """The ``_Endpointer`` keyword arguments for ``p`` (the seam to add: ``tail_deep_ms`` / ``tail_quiet_ms`` /
    ``tail_clean_ms``); ``{}`` = today's module globals, byte-identical."""
    if p is None:
        return {}
    return {"tail_deep_ms": p.deep_ms, "tail_quiet_ms": p.quiet_ms, "tail_clean_ms": p.clean_ms}


# ── the cache file (the last panel profile, so a restart or an offline server keeps it) ─────────────────────────────────────
def load_cache(path: str, *, speculative_tail_ms: int = 0) -> Optional[Profile]:
    try:
        p = Path(os.path.expanduser(path))
        if p.stat().st_size > _MAX_FILE_BYTES:
            return None
        raw = json.loads(p.read_text())
        if not isinstance(raw, Mapping) or raw.get("v") != 1:
            return None
        return parse_profile(raw.get("panel"), speculative_tail_ms=speculative_tail_ms)
    except Exception:  # noqa: BLE001 - missing, unreadable or corrupt: no cached profile
        return None


def save_cache(path: str, prof: Profile) -> bool:
    """Atomic write, 0600 from the first byte. False on any failure (the in-memory profile still applies)."""
    tmp = None
    try:
        p = Path(os.path.expanduser(path))
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(json.dumps({"v": 1, "panel": prof.as_dict()}))
        os.replace(tmp, p)
        return True
    except Exception:  # noqa: BLE001
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


class PaceHolder:
    """What the next recording would be built with. Monotonic times only. In-memory except the panel profile's cache.

    The window profile is the carried verified speaker's: it holds only for a follow-up / conversation recording before its
    ttl, and ANY wake drops it (a new wake can be anyone)."""

    def __init__(self, *, mode: str = "off", path: str = LISTEN_PACE_FILE, speculative_tail_ms: int = 0,
                 cached: Optional[Profile] = None):
        self.mode = mode if mode in MODES else "off"
        self.path = path
        self.spec = int(speculative_tail_ms)
        self.panel: Optional[Profile] = None
        self.cached = cached
        self.window: Optional[Profile] = None
        self.window_until = 0.0
        self.apply = False
        self.token = ""
        self._on_disk: Optional[Profile] = cached      # the panel profile the cache file holds (what a restart would apply)

    def note_frame(self, frame, now: float) -> bool:
        """Take a done frame's ``listen`` key. Returns True when one was taken; a malformed one changes nothing. Nothing is
        cached here: receiving a profile is not using it (``choose`` caches what it actually applies)."""
        if self.mode == "off":
            return False
        lst = parse_listen(frame, speculative_tail_ms=self.spec)
        if lst is None:
            return False
        self.apply, self.token = lst.apply, lst.token
        if lst.panel is not None:
            self.panel = lst.panel
        if lst.next is not None and math.isfinite(now):
            self.window, self.window_until = lst.next, now + lst.ttl_s
        else:
            self.window, self.window_until = None, 0.0
        return True

    def on_wake(self) -> None:
        self.window, self.window_until = None, 0.0

    def would(self, win: str, now: float) -> "tuple[Optional[Profile], str]":
        """(profile, src) this holder would build the next ``win`` recording with, ignoring the mode's apply rule."""
        if win in ("followup", "conversation") and self.window is not None and now < self.window_until:
            return self.window, "window"
        if self.panel is not None:
            return self.panel, "panel"
        if self.cached is not None:
            return self.cached, "cache"
        return None, "env"

    def choose(self, win: str, now: float) -> "tuple[Optional[Profile], str]":
        """(profile actually to use, src). Only ``LISTEN_PACE=on`` with a server frame that said ``apply`` uses a profile; a
        cached profile applies only in ``on`` (the server said apply before). Everything else is today's tails.
        The cache follows APPLICATION: a panel profile returned here is written to it when it differs from what the file holds
        (a shadow or refused profile never is, so a restart only ever re-applies a profile that was really used)."""
        if self.mode != "on":
            return None, "env"
        prof, src = self.would(win, now)
        if prof is None or (src != "cache" and not self.apply):
            return None, "env"
        if src == "panel" and prof != self._on_disk and save_cache(self.path, prof):
            self._on_disk = prof               # a failed write is retried at the next application
        return prof, src


def outcome_body(*, token: str, win: str, rule: str, close_ms: int, quiet_ms: int, fall: str, still_talking: int,
                 talk_chunks: int, obs_ms: int, muted_ms: int, reflex: bool = False, frozen: str = "",
                 level_used: Optional[int] = None) -> Optional[dict]:
    """The ``POST /api/voice/turn_outcome`` body for one turn: integers and fixed enums only. ``None`` when there is no valid
    token or a field is outside its enum (the server would refuse it anyway; better not to send it)."""
    try:
        if not isinstance(token, str) or not TOKEN_RE.match(token):
            return None
        if win not in WINS or rule not in RULES or fall not in FALLS or (frozen and frozen not in FREEZES):
            return None
        nums = {"close_ms": close_ms, "quiet_ms": quiet_ms, "still_talking": still_talking, "talk_chunks": talk_chunks,
                "obs_ms": obs_ms, "muted_ms": muted_ms}
        for v in nums.values():
            _int(v)
        if still_talking not in (0, 1):
            return None
        body = {"v": 1, "token": token, "win": win, "rule": rule, "fall": fall, "reflex": 1 if reflex else 0,
                "frozen": frozen or "-", **nums}
        if level_used is not None:
            _int(level_used)
            body["level_used"] = level_used
        return body
    except (ValueError, TypeError):
        return None


def freeze_reason(*, media: Optional[bool], night: bool = False, first_wake: bool = False, speculation: bool = False,
                  capped: bool = False) -> str:
    """The first freeze that applies ("" = none). Unknown media state (None) freezes: no evidence on a guess."""
    if media is None or media:
        return "media"
    if night:
        return "night"
    if first_wake:
        return "first_wake"
    if speculation:
        return "speculation"
    if capped:
        return "cap"
    return ""


def pace_line(*, mode: str, used: Optional[Profile], used_src: str, would: Optional[Profile], would_src: str, win: str,
              frozen: str = "") -> str:
    """``PACE mode=.. win=.. used=<deep>/<quiet>/<clean> src=.. would=<deep>/<quiet>/<clean> would_src=.. level=.. frozen=..``
    (-1 = today's tails / unknown). Integers and fixed enums only."""
    def tails(p: Optional[Profile]) -> str:
        return "-1/-1/-1" if p is None else f"{p.deep_ms}/{p.quiet_ms}/{p.clean_ms}"
    lvl = would.level if would is not None and would.level is not None else -99
    return "PACE mode=%s win=%s used=%s src=%s would=%s would_src=%s level=%s frozen=%s" % (
        mode if mode in MODES else "off", win if win in WINS else "barge", tails(used),
        used_src if used_src in SRCS else "env", tails(would), would_src if would_src in SRCS else "env",
        "-" if lvl == -99 else lvl, frozen if frozen in FREEZES else "-")
