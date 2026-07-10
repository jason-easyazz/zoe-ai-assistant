"""First-turn-of-day spoken greeting for the voice lane.

When enabled (``ZOE_VOICE_GREETING_ENABLED``), the first voice turn on a given
local day yields a short time-of-day greeting phrase ("Good morning" /
"Good afternoon" / "Good evening") via :func:`greeting_prefix`. The turn_stream
handler emits it as its own leading, pre-warmed (therefore ~instant) audio chunk
ahead of the real answer, keyed by panel/speaker so it fires once per local day.

Last-greeted local date per key is persisted to a small JSON file so a
``zoe-data`` restart mid-day does not re-greet, backed by an in-memory mirror so
an unwritable store degrades to "at most one extra greeting per restart" rather
than re-greeting every turn. All state I/O is best-effort: any error degrades to
"no greeting", never an exception on the turn path.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_ZOE_TIMEZONE = os.environ.get("ZOE_TIMEZONE", "Australia/Perth")
_STATE_LOCK = threading.Lock()
# In-memory mirror of "already greeted <key> today". Guards the case where the
# on-disk store is unwritable (read-only/full path): without it a failed persist
# would re-greet on every turn. Process-scoped, so at worst one extra greeting
# per restart when the disk write is broken.
_GREETED_MEM: dict[str, str] = {}


def _enabled() -> bool:
    return os.environ.get("ZOE_VOICE_GREETING_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def _state_path() -> str:
    return os.environ.get(
        "ZOE_VOICE_GREETING_STATE_PATH",
        os.path.expanduser("~/.zoe/voice_greeting_state.json"),
    )


def _now_local(now: Optional[datetime.datetime] = None) -> datetime.datetime:
    if now is not None:
        return now
    try:
        from zoneinfo import ZoneInfo  # py3.9+
        return datetime.datetime.now(ZoneInfo(_ZOE_TIMEZONE))
    except Exception:
        return datetime.datetime.now()


def greeting_for_hour(hour: int) -> str:
    """Time-of-day greeting phrase (no trailing punctuation)."""
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 18:
        return "Good afternoon"
    return "Good evening"


def _load_state() -> dict:
    try:
        with open(_state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # corrupt/unreadable → start fresh, never break the turn
        logger.debug("voice_greeting: state read failed (%s), ignoring", exc)
        return {}


def _save_state(state: dict) -> bool:
    """Persist state atomically. Returns True on durable write, False otherwise.

    A False here means the once-per-day state is NOT durable — the in-memory guard
    still prevents re-greeting within this process, but a restart will re-greet
    once. That degradation is inherent to an unwritable path, so we log it LOUDLY
    (WARNING) rather than swallowing it, so a misconfigured
    ``ZOE_VOICE_GREETING_STATE_PATH`` gets noticed and fixed.
    """
    path = _state_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)  # atomic
        return True
    except Exception as exc:
        logger.warning(
            "voice_greeting: could not persist state to %s (%s) — greeting will "
            "not survive a restart until this path is writable", path, exc,
        )
        return False


def greeting_prefix(user_id: str, *, now: Optional[datetime.datetime] = None) -> str:
    """Return a greeting phrase for the user's FIRST turn of the local day, else "".

    Records the greeting so subsequent turns the same day return "". Best-effort:
    disabled flag, missing user, or any state error → "".
    """
    if not _enabled() or not user_id:
        return ""
    local = _now_local(now)
    today = local.date().isoformat()
    try:
        with _STATE_LOCK:
            # In-memory guard first — survives an unwritable on-disk store so a
            # persist failure can't re-greet every turn.
            if _GREETED_MEM.get(user_id) == today:
                return ""
            state = _load_state()
            if state.get(user_id) == today:
                _GREETED_MEM[user_id] = today
                return ""  # already greeted today
            _GREETED_MEM[user_id] = today
            state[user_id] = today
            _save_state(state)
    except Exception as exc:
        logger.debug("voice_greeting: prefix check failed (%s)", exc)
        return ""
    phrase = greeting_for_hour(local.hour)
    logger.info("voice_greeting: greeting %s for key=%s (%s)", phrase, user_id, today)
    return phrase
