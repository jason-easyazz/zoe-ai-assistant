"""First-turn-of-day spoken greeting for the voice lane.

When enabled (``ZOE_VOICE_GREETING_ENABLED``), the first voice turn a user takes
on a given local day gets a short time-of-day greeting ("Good morning." /
"Good afternoon." / "Good evening.") prepended as its own sentence, so the
sentence-streamed TTS renders it as a separate — and pre-warmed, therefore
instant — clip ahead of the real answer.

State (last-greeted local date per user) is persisted to a small JSON file so a
``zoe-data`` restart mid-day does not re-greet. The store is intentionally tiny
and best-effort: any read/write failure degrades to "no greeting", never an error
on the turn path.
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


def _save_state(state: dict) -> None:
    path = _state_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)  # atomic
    except Exception as exc:
        logger.debug("voice_greeting: state write failed (%s), continuing", exc)


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
            state = _load_state()
            if state.get(user_id) == today:
                return ""  # already greeted today
            state[user_id] = today
            _save_state(state)
    except Exception as exc:
        logger.debug("voice_greeting: prefix check failed (%s)", exc)
        return ""
    phrase = greeting_for_hour(local.hour)
    logger.info("voice_greeting: greeting %s for user=%s (%s)", phrase, user_id, today)
    return phrase


def apply_greeting(reply: str, user_id: str, *, now: Optional[datetime.datetime] = None) -> str:
    """Prepend the first-turn-of-day greeting to ``reply`` as its own sentence.

    Kept as a separate leading sentence so the sentence-streamed TTS renders the
    (pre-warmed, cached) greeting clip instantly ahead of the answer. No-op when
    disabled or not the first turn of the day.
    """
    reply = (reply or "").strip()
    if not reply:
        return reply
    phrase = greeting_prefix(user_id, now=now)
    if not phrase:
        return reply
    return f"{phrase}. {reply}"
