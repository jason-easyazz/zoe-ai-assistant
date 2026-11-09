"""The household's DREAMING WINDOW: when Zoe may start her night session, how quiet the house must have been, and when she must be awake again.

A HOUSEHOLD SETTING, not code constants (owner, 2026-11-09: "not everyone will be the same"). Stdlib only, so the night scripts (``scripts/night/night_window.py``) and the
API (``routers/stubs.py``) share ONE implementation:

  * ``earliest`` (default 00:30) / ``latest`` (02:30): the session may START between them, once the house has been quiet for ``quiet_min`` (30) minutes;
  * ``awake_by`` (05:15): Zoe is awake and healthy by then - one hard deadline.

Source of truth: the ``dream_window`` row of ``system_preferences`` (JSON), written only by an adult household admin through ``PUT /api/settings/dream-window`` or a
verified-adult-voice request; every write also mirrors the validated window to ``~/.zoe/dream-window.json`` (no secrets), which is what the night session and
``night_busy.sh`` read EACH NIGHT. The env (``ZOE_DREAM_*``) is only the fallback default when nothing valid is stored. An invalid stored value falls back to the defaults and the
errors are returned so the morning report can say so - a bad setting never silently shortens or lengthens the night.

Validation (``validate``): HH:MM times between 21:00 and 08:00 on the night clock; ``earliest`` <= ``latest``; ``awake_by`` - ``earliest`` >= ``MIN_WINDOW_MIN`` (90);
``awake_by`` >= ``latest`` + ``MIN_CHAIN_MIN`` (150: backup/export, sleep, listen, think, wake with its health checks); ``awake_by`` not inside the nightly voice-gate artifact slot
(04:10-04:52, docs/knowledge/night-window.md); ``quiet_min`` 10-180.

Spoken: ``parse_utterance`` ("do your dreaming between 1 and 5", "when do you dream?"), ``answer`` / ``confirm`` for Zoe's reply. A change by voice needs a VERIFIED ADULT voice
(``may_change``) and Zoe reads the new window back; a guest, a child or an unverified voice can only ASK.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

KEY = "dream_window"
MIN_WINDOW_MIN = 90
MIN_CHAIN_MIN = 150
VOICE_GATE_SLOT = (4 * 60 + 10, 4 * 60 + 52)            # minutes after midnight: the nightly voice-regression artifact slot (04:10-04:52)
NIGHT_FROM, NIGHT_TO = 21 * 60, 8 * 60                    # the night clock: 21:00 .. 08:00 next morning
FIELDS = ("earliest", "latest", "awake_by")
DEFAULTS = {"earliest": "00:30", "latest": "02:30", "awake_by": "05:15", "quiet_min": 30}
ADULT_ROLES = ("admin", "owner")


def mirror_path() -> Path:
    return Path(os.environ.get("ZOE_DREAM_WINDOW_FILE") or (Path.home() / ".zoe" / "dream-window.json"))


def env_defaults() -> dict:
    """The fallback defaults: the code's, overridden by ``ZOE_DREAM_*`` if (and only if) the override is itself valid."""
    d = dict(DEFAULTS)
    cand = {"earliest": os.environ.get("ZOE_DREAM_EARLIEST"), "latest": os.environ.get("ZOE_DREAM_LATEST"), "awake_by": os.environ.get("ZOE_DREAM_AWAKE_BY"),
            "quiet_min": os.environ.get("ZOE_DREAM_QUIET_MIN")}
    cand = {k: v for k, v in cand.items() if v}
    if cand:
        merged = {**d, **{k: (int(v) if k == "quiet_min" and str(v).isdigit() else v) for k, v in cand.items()}}
        if not validate(merged, fallback=False)[1]:
            return merged
    return d


def parse_hm(v: Any) -> Optional[int]:
    """'HH:MM' -> minutes after midnight, else None."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(v if v is not None else ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def fmt_hm(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def night_min(minutes: int) -> int:
    """Minutes on the night clock (21:00 = 1260 ... 08:00 next morning = 1920): a clock time before 21:00 belongs to the NEXT morning."""
    return minutes + 1440 if minutes < NIGHT_FROM else minutes


def validate(d: Any, *, fallback: bool = True) -> tuple:
    """-> (window, errors). ``window`` is always usable: with errors it is the (env) defaults when ``fallback``, else the input as far as it parsed. Never raises."""
    base = dict(DEFAULTS) if not fallback else env_defaults()
    errs: list = []
    if not isinstance(d, dict):
        return base, ["not an object"] if d is not None else []
    got: dict = {}
    for k in FIELDS:
        raw = d.get(k, base[k])
        m = parse_hm(raw)
        if m is None:
            errs.append(f"{k}: not a time (HH:MM)")
            continue
        n = night_min(m)
        if not (NIGHT_FROM <= n <= 1440 + NIGHT_TO):
            errs.append(f"{k}: {fmt_hm(m)} is outside the night (21:00-08:00)")
            continue
        got[k] = m
    q = d.get("quiet_min", base["quiet_min"])
    try:
        quiet = int(q)
        if not (10 <= quiet <= 180):
            raise ValueError
    except (TypeError, ValueError):
        errs.append("quiet_min: 10-180 minutes")
        quiet = base["quiet_min"]
    if not errs:
        e, l, a = (night_min(got[k]) for k in FIELDS)
        if e > l:
            errs.append("earliest start is after the latest start")
        if a - e < MIN_WINDOW_MIN:
            errs.append(f"the window must allow at least {MIN_WINDOW_MIN} minutes (awake-by minus earliest start)")
        if a < l + MIN_CHAIN_MIN:
            errs.append(f"awake-by must be at least {MIN_CHAIN_MIN} minutes after the latest start (backup, sleep, listen, think, wake)")
        if VOICE_GATE_SLOT[0] <= got["awake_by"] < VOICE_GATE_SLOT[1]:
            errs.append("awake-by falls inside the nightly voice-check slot (04:10-04:52)")
    if errs:
        return (dict(base) if fallback else {**{k: fmt_hm(v) for k, v in got.items()}, "quiet_min": quiet}), errs
    return {**{k: fmt_hm(got[k]) for k in FIELDS}, "quiet_min": quiet}, []


def load_mirror(path: Optional[Path] = None) -> tuple:
    """The window the night session uses: (window, errors, source) - 'stored' | 'default'. A missing file is the defaults with NO error; an unreadable or invalid one is the
    defaults WITH the errors (the morning report prints them)."""
    p = path or mirror_path()
    try:
        raw = json.loads(p.read_text())
    except FileNotFoundError:
        return env_defaults(), [], "default"
    except (OSError, ValueError):
        return env_defaults(), ["the stored dreaming window is unreadable; using the defaults"], "default"
    w, errs = validate(raw)
    return w, errs, ("default" if errs else "stored")


def save_mirror(window: dict, path: Optional[Path] = None) -> None:
    p = path or mirror_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".dream-window.")
    with os.fdopen(fd, "w") as fh:
        json.dump(window, fh)
    os.replace(tmp, p)


def start_gate(now_min: int, window: dict, quiet_for_min: float) -> str:
    """Whether the session may start NOW (clock minutes after midnight): ``too_early`` before ``earliest``, ``too_late`` after ``latest`` (the session is skipped for tonight and the
    stages carry), ``wait`` while the house has not been quiet for ``quiet_min``, else ``start``."""
    n, e, l = night_min(now_min), night_min(parse_hm(window["earliest"]) or 0), night_min(parse_hm(window["latest"]) or 0)
    if n < e:
        return "too_early"
    if n > l:
        return "too_late"
    return "start" if quiet_for_min >= int(window["quiet_min"]) else "wait"


# ── who may change it ────────────────────────────────────────────────────────────────────────────

def may_change(role: Any, *, via_voice: bool = False, voice_verified: bool = False) -> bool:
    """Adult household admins only (the owner role). Never a guest or a child. By VOICE the speaker must also be verified - an unrecognised voice can only ask."""
    if not isinstance(role, str) or role.strip().lower() not in ADULT_ROLES:
        return False
    return voice_verified if via_voice else True


# ── spoken ───────────────────────────────────────────────────────────────────────────────────────

_ASK = re.compile(r"\bwhen\b.*\b(do|will|are)\b.*\b(you|zoe)\b.*\b(dream|sleep)\b|\bwhat\b.*\b(dream(ing)?|sleep(ing)?)\b.*\b(window|time|hours)\b|\bwhen\b.*\b(dream(ing)?|night)\b.*\b(window|session|start|run)\b", re.I)
_SET = re.compile(r"\b(dream(ing)?|sleep(ing)?|night (session|window)|work at night|do your (dreaming|thinking))\b.*\bbetween\b\s+(?P<a>[^\s,]+(?:\s*(?:am|pm|a\.m\.|p\.m\.))?)\s+and\s+(?P<b>[^\s,.?!]+(?:\s*(?:am|pm|a\.m\.|p\.m\.))?)", re.I)
_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}


def _hour(tok: str) -> Optional[int]:
    """'1', 'one', '1am', '11pm', '1:30', 'midnight' -> minutes after midnight on the night clock, or None. A bare 1-8 is the small hours; 9-11 bare is ambiguous -> None."""
    t = tok.strip().lower().replace(".", "")
    if t == "midnight":
        return 0
    m = re.fullmatch(r"(\d{1,2}|" + "|".join(_WORDS) + r")(?::(\d{2}))?\s*(am|pm)?", t)
    if not m:
        return None
    h = _WORDS[m.group(1)] if m.group(1) in _WORDS else int(m.group(1))
    mi = int(m.group(2) or 0)
    ap = m.group(3)
    if mi > 59 or h > 12 or h == 0 and not ap:
        return None
    if ap == "pm":
        h = h % 12 + 12
    elif ap == "am":
        h = h % 12
    elif 9 <= h <= 11:
        return None
    elif h == 12:
        h = 0
    return h * 60 + mi


def parse_utterance(text: str) -> Optional[dict]:
    """``{"kind": "ask"}`` for "when do you dream?", ``{"kind": "set", "earliest": "01:00", "awake_by": "05:00"}`` for "do your dreaming between 1 and 5", else None.
    "between 1 and 5" is the span Zoe may be at work: she may start from the first time and must be awake by the second."""
    t = (text or "").strip()
    m = _SET.search(t)
    if m:
        a, b = _hour(m.group("a")), _hour(m.group("b"))
        if a is not None and b is not None:
            return {"kind": "set", "earliest": fmt_hm(a), "awake_by": fmt_hm(b)}
        return {"kind": "set_unclear"}
    if _ASK.search(t):
        return {"kind": "ask"}
    return None


def _say(hm: str) -> str:
    h, m = divmod(parse_hm(hm) or 0, 60)
    ap = "am" if h < 12 else "pm"
    return f"{h % 12 or 12}{':%02d' % m if m else ''} {ap}"


def answer(window: dict, errors: Optional[list] = None) -> str:
    s = (f"I dream between {_say(window['earliest'])} and {_say(window['awake_by'])}: I start once the house has been quiet for {window['quiet_min']} minutes, "
         f"no earlier than {_say(window['earliest'])} and no later than {_say(window['latest'])}, and I'm back awake by {_say(window['awake_by'])}.")
    return s + (" The window that was saved did not check out, so I'm using my usual one." if errors else "")


def apply_spoken(text: str, current: dict, *, role: Any, voice_verified: bool) -> dict:
    """The whole spoken flow, pure: -> {"reply": str, "window": new window or None (unchanged), "errors": [...]}. A change needs an adult admin whose voice was verified; the reply
    reads the new window back. An invalid span is refused with the reason and nothing changes."""
    intent = parse_utterance(text)
    if intent is None:
        return {"reply": "", "window": None, "errors": []}
    if intent["kind"] == "ask":
        return {"reply": answer(current), "window": None, "errors": []}
    if intent["kind"] == "set_unclear":
        return {"reply": "Which hours? For example: do your dreaming between 1 and 5.", "window": None, "errors": []}
    if not may_change(role, via_voice=True, voice_verified=voice_verified):
        return {"reply": "Only an adult I recognise by voice can change when I dream. " + answer(current), "window": None, "errors": ["not allowed"]}
    new = dict(current, earliest=intent["earliest"], awake_by=intent["awake_by"])
    latest_cap = (night_min(parse_hm(intent["awake_by"]) or 0) - MIN_CHAIN_MIN) % 1440
    if night_min(parse_hm(new["latest"]) or 0) > night_min(latest_cap):
        new["latest"] = fmt_hm(latest_cap)                       # "between 1 and 5": the last moment she can start and still be back by 5
    w, errs = validate(new, fallback=False)
    if errs:
        return {"reply": "I can't dream between those hours: " + "; ".join(errs) + ". " + answer(current), "window": None, "errors": errs}
    return {"reply": "Okay. " + answer(w).replace("I dream between", "I'll dream between", 1), "window": w, "errors": []}
