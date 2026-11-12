"""A member's OWN pendant settings ("My pendant" in Settings), on ``omi_voice_settings`` (migrations 0055 + 0077).

THE RULE: the operator flag is the MASTER SWITCH and the CEILING; a member's setting can only NARROW it, never widen it. Every
``effective_*`` below is ``operator AND member`` (or ``min(operator, member)`` for days), so a member who turns something "on" while the
operator has it off gets nothing, and an absent row (or an unreadable table) means exactly today's behaviour.

  setting                 column (default)          operator flag (master / ceiling)          read by
  pause recording sync    sync_paused (0)           ZOE_OMI_SYNC_ENABLED                      routers/omi_recordings._upload_owner (409, Pi keeps audio)
  tag voices              speaker_tag_enabled (1)   ZOE_OMI_SPEAKER_TAG                       omi_recordings._transcribe_one (before _diarise)
  learn the voices I name learning_enabled (1)      ZOE_OMI_LEARN_VOICES                      omi_voices.may_learn (0055, unchanged)
  keep audio (days)       keep_audio_days (NULL)    ZOE_OMI_AUDIO_RETENTION_DAYS (ceiling)    omi_recordings.store_recording
  offer me a day review   day_review_enabled (1)    ZOE_OMI_DAY_REVIEW                        omi_day_review (per member-day)
  the recording notice    notice_ack_at (NULL)      -                                         omi_linking (told once, at link time)

Children: nothing here can make a child's pendant tagged or learned - ``_diarise`` and ``may_learn`` still require a positive adult
(D14), and a child cannot self-link at all (``omi_devices.self_assign``). Erasure: ``omi_voices.forget_user`` deletes the row.
Pinned by ``tests/test_omi_linking.py``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: column -> default (today's behaviour). ``learning_enabled`` is 0055's; the rest are 0077's.
DEFAULTS: dict[str, Any] = {"learning_enabled": 1, "sync_paused": 0, "speaker_tag_enabled": 1, "keep_audio_days": None,
                            "day_review_enabled": 1, "notice_ack_at": None}
#: the switches a member may set through ``PUT /api/omi/voices/settings`` (learning keeps its own setter in omi_voices)
BOOL_FIELDS = ("sync_paused", "speaker_tag_enabled", "day_review_enabled")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _rows(db, sql: str, params: tuple = ()) -> list:
    cur = await db.execute(sql, params)
    return list(await cur.fetchall())


def _get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return DEFAULTS[key]


async def read(db, user_id: str) -> dict:
    """The member's stored values, defaults filled in. A missing table or a pre-0077 row reads as the defaults (today's behaviour)."""
    out = {**DEFAULTS, **{k: bool(DEFAULTS[k]) for k in ("learning_enabled", *BOOL_FIELDS)}}
    try:
        rows = await _rows(db, "SELECT * FROM omi_voice_settings WHERE user_id = ?", (user_id,))
    except Exception as exc:  # noqa: BLE001 - unreadable = defaults: never widens anything (the operator flag still decides)
        logger.debug("omi_member_settings: unreadable (%s)", type(exc).__name__)
        return out
    if rows:
        for k in DEFAULTS:
            v = _get(rows[0], k)
            out[k] = DEFAULTS[k] if v is None and DEFAULTS[k] is not None else v
    for k in ("learning_enabled", *BOOL_FIELDS):
        out[k] = bool(out[k])
    return out


async def read_uid(user_id: str) -> dict:
    """``read`` on its own connection (for the hot paths that hold none)."""
    try:
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            return await read(db, user_id)
    except Exception:  # noqa: BLE001
        return dict(DEFAULTS)


async def write(db, user_id: str, **changes: Any) -> dict:
    """Upsert only the named columns (unknown names are a programming error). Commits."""
    bad = set(changes) - set(DEFAULTS)
    if bad:
        raise ValueError(f"unknown setting(s): {sorted(bad)}")
    now = _now()
    if changes:
        cols = list(changes)
        sets = ", ".join(f"{c} = ?" for c in cols)
        cur = await db.execute(f"UPDATE omi_voice_settings SET {sets}, updated_at = ? WHERE user_id = ?",
                               (*[changes[c] for c in cols], now, user_id))
        if int(getattr(cur, "rowcount", 0) or 0) == 0:
            await db.execute(f"INSERT INTO omi_voice_settings (user_id, updated_at, {', '.join(cols)}) "
                             f"VALUES (?, ?, {', '.join('?' * len(cols))})", (user_id, now, *[changes[c] for c in cols]))
        await db.commit()
    return await read(db, user_id)


# --- the operator side (master switch / ceiling) ----------------------------------------------------------------------------
def operator() -> dict:
    """What the operator flags allow, per setting. Read per call (flags are per-call env reads everywhere in this feature)."""
    import omi_day_review
    import omi_diarize
    import omi_recordings
    import omi_voices
    return {"sync": omi_recordings.enabled(), "speaker_tag": omi_diarize.mode() != "off", "learn_voices": omi_voices.learn_enabled(),
            "keep_audio_days_max": omi_recordings.retention_days(), "day_review": omi_day_review.mode() != "off"}


def clamp_days(member_days: Optional[int], ceiling: int) -> int:
    """The effective retention: the operator's ceiling unless the member chose FEWER days (never more, never below one)."""
    if member_days is None:
        return ceiling
    try:
        return max(1, min(int(member_days), ceiling))
    except (TypeError, ValueError):
        return ceiling


def effective(stored: dict, op: Optional[dict] = None) -> dict:
    """``operator AND member`` for every switch, ``min`` for the days. Pure (given ``op``)."""
    op = op if op is not None else operator()
    return {"sync": bool(op["sync"]) and not bool(stored["sync_paused"]),
            "speaker_tag": bool(op["speaker_tag"]) and bool(stored["speaker_tag_enabled"]),
            "learn_voices": bool(op["learn_voices"]) and bool(stored["learning_enabled"]),
            "keep_audio_days": clamp_days(stored["keep_audio_days"], int(op["keep_audio_days_max"])),
            "day_review": bool(op["day_review"]) and bool(stored["day_review_enabled"])}


async def sync_paused(user_id: str) -> bool:
    return bool((await read_uid(user_id))["sync_paused"])


def _schema_missing(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return type(exc).__name__ in ("UndefinedTableError", "UndefinedColumnError") or "no such table" in msg or "no such column" in msg


async def speaker_tag_on(user_id: str) -> bool:
    """The member's own switch only; the operator mode is checked by the caller (``_tag_mode``), exactly as before.
    FAILS CLOSED, unlike ``read``: a settings read that errors means do NOT tag (the first pass AND the night re-pass). Only a pre-0077
    schema (the table / column does not exist yet, so nobody can have turned it off) reads as the default."""
    try:
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            rows = await _rows(db, "SELECT speaker_tag_enabled FROM omi_voice_settings WHERE user_id = ?", (user_id,))
    except Exception as exc:  # noqa: BLE001
        if _schema_missing(exc):
            return bool(DEFAULTS["speaker_tag_enabled"])
        logger.warning("omi_member_settings: tag preference unreadable, not tagging (%s)", type(exc).__name__)
        return False
    if not rows:
        return bool(DEFAULTS["speaker_tag_enabled"])
    v = _get(rows[0], "speaker_tag_enabled")
    return bool(DEFAULTS["speaker_tag_enabled"] if v is None else v)


async def retention_days_for(user_id: str, ceiling: int) -> int:
    return clamp_days((await read_uid(user_id))["keep_audio_days"], ceiling)


async def day_review_on(user_id: str) -> bool:
    return bool((await read_uid(user_id))["day_review_enabled"])


async def acknowledge_notice(db, user_id: str) -> str:
    """Record that the member heard the recording notice (first time only; returns the stored time)."""
    cur = (await read(db, user_id))["notice_ack_at"]
    if cur:
        return cur
    return (await write(db, user_id, notice_ack_at=_now()))["notice_ack_at"]
