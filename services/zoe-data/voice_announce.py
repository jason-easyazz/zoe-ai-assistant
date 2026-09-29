"""Daemon-facing spoken-announcement queue (P-W2.3).

Why this exists: W2's spoken morning brief "succeeded" twice with no audio.
The `panel_announce` UI action reaches the kiosk BROWSER, which shows a toast
and then fire-and-forget fetches `/api/voice/speak` — but the kiosk is a guest
session with no device token, so the fetch 401'd and four silent swallow
points ate the failure. The proven speaker is the Pi voice DAEMON
(`scripts/setup/zoe_voice_daemon.py`): device-token auth, pyaudio playback,
barge-in, echo-suppression cooldowns. This module is the server side of the
server→daemon announcement lane:

  * `enqueue_announcement(db, ...)` — called by `proactive/engine.py`'s spoken
    path IN ADDITION to the `panel_announce` toast (additive, never blocks the
    push; see the P-W2.2 contract in services/zoe-data/AGENTS.md).
  * `claim_announcements(db, panel_id=...)` — backs the device-token-only
    `GET /api/voice/announcements` endpoint in `routers/voice_tts.py`. Claims
    are atomic (UPDATE ... WHERE delivered_at IS NULL, rowcount-checked), so
    overlapping polls can never return the same row twice (no double-speak).
  * `mark_played(db, ...)` — backs the device-token-only
    `POST /api/voice/announcements/{id}/played` ACK the daemon sends after
    playback returns. `delivered_at` only means CLAIMED (before TTS or
    playback); `played_at` is the evidence it was heard.
  * TTL: an announcement older than `ZOE_ANNOUNCE_TTL_S` (default 120 s) is
    marked `expired = 1` and never returned — a stale "good morning" spoken at
    noon is worse than silence.

Panel matching: by default the claim is NOT restricted to rows whose
`panel_id` equals the caller's token panel. The kiosk answers to multiple ids
(generated `panel_xxxx` browser ids vs the registered `zoe-touch-pi` device
token — the alias-mismatch class fixed panel-side in #817), and presence
(`proactive/presence.py`) records the BROWSER id while the daemon holds the
DEVICE id, so strict equality would silently deliver nothing. With a single
household speaker, claim-any is correct; the atomic claim still guarantees
exactly-once if a second daemon ever appears. Set `ZOE_ANNOUNCE_STRICT_PANEL`
to require an exact panel match (multi-speaker future). Rows of a
`PANEL_SCOPED_TRIGGERS` trigger (a member's full brief spoken because they were
seen on one panel) require the exact match in either mode.

Timestamps are TEXT UTC (``%Y-%m-%dT%H:%M:%SZ``) matching the proactive
tables; ISO-Z strings compare in time order. The claim response carries
`expires_in_s` (computed server-side) so the Pi never has to compare its own
clock against the Jetson's.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_DEFAULT_TTL_S = 120
# Triggers whose rows are ALWAYS panel-scoped, even in the default claim-any
# mode: only a daemon whose device-token panel equals the row's ``panel_id`` may
# claim them. Brief-on-arrival (``proactive/arrival.py``) speaks a member's FULL
# brief because owner presence was seen on ONE panel — another panel's speaker
# must never play it. arrival.py queues it only for a panel that holds a live
# device token, so the ids match by construction.
PANEL_SCOPED_TRIGGERS: tuple[str, ...] = ("morning_checkin_arrival",)

# Hard cap per claim; the engine enqueues one row per spoken notification, so
# anything larger than a handful means a backlog that should expire, not play.
_CLAIM_LIMIT = 5
# The 07:30 brief's trigger (proactive/triggers/morning_checkin.py).
SCHEDULED_BRIEF_TRIGGER = "morning_checkin"


def _ttl_s() -> int:
    """TTL in seconds (ZOE_ANNOUNCE_TTL_S, default 120). Read per call."""
    raw = os.environ.get("ZOE_ANNOUNCE_TTL_S", "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_TTL_S
    except ValueError:
        value = _DEFAULT_TTL_S
    return max(1, value)


def _strict_panel() -> bool:
    """ZOE_ANNOUNCE_STRICT_PANEL, default OFF (see module docstring)."""
    return os.environ.get("ZOE_ANNOUNCE_STRICT_PANEL", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS_FMT)


async def enqueue_announcement(
    db,
    *,
    user_id: str,
    message: str,
    panel_id: str | None = None,
    trigger_type: str = "",
    ttl_s: int | None = None,
    commit: bool = True,
) -> str:
    """Insert one pending spoken announcement; returns its id.

    Raises on bad input or DB failure — the CALLER (the proactive engine's
    never-raise adapter) owns swallowing, so a queue failure is logged there
    with an outcome instead of vanishing.
    """
    text = str(message or "").strip()
    if not text:
        raise ValueError("announcement message is empty")
    ttl = max(1, int(ttl_s)) if ttl_s else _ttl_s()
    now = _now()
    ann_id = uuid.uuid4().hex[:16]
    await db.execute(
        """INSERT INTO voice_announcements
               (id, user_id, panel_id, message, trigger_type, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            ann_id,
            user_id,
            panel_id,
            text,
            trigger_type or "",
            _fmt(now),
            _fmt(datetime.fromtimestamp(now.timestamp() + ttl, tz=timezone.utc)),
        ),
    )
    if commit:
        await db.commit()
    return ann_id


def _expires_in_s(expires_at: str, now: datetime) -> float:
    try:
        exp = datetime.strptime(str(expires_at), _TS_FMT).replace(tzinfo=timezone.utc)
        return max(0.0, (exp - now).total_seconds())
    except (ValueError, TypeError):
        # Unparseable expiry — treat as already at the edge of its TTL so the
        # daemon speaks it now or never (it was valid at claim time by SQL).
        return 0.0


async def claim_announcements(db, *, panel_id: str, limit: int = _CLAIM_LIMIT) -> list[dict]:
    """Atomically claim pending, unexpired announcements; mark stale ones expired.

    Exactly-once: each candidate is claimed with
    ``UPDATE ... SET delivered_at = ? WHERE id = ? AND delivered_at IS NULL``
    and only rows where rowcount == 1 are returned — a concurrent poll that
    lost the race gets rowcount 0 and skips the row. Expired rows are MARKED
    (`expired = 1`) and never returned, so a stale announcement is never
    spoken and never lingers as "pending" forever.
    """
    now = _now()
    now_s = _fmt(now)
    limit = max(1, min(int(limit), _CLAIM_LIMIT))

    # 1) Mark (never play) anything past its TTL.
    await db.execute(
        """UPDATE voice_announcements SET expired = 1
           WHERE delivered_at IS NULL AND expired = 0 AND expires_at <= ?""",
        (now_s,),
    )

    # 2) Candidate rows (oldest first, so briefs play in order).
    if _strict_panel():
        cursor = await db.execute(
            """SELECT id, user_id, message, trigger_type, expires_at FROM voice_announcements
               WHERE delivered_at IS NULL AND expired = 0 AND expires_at > ?
                 AND panel_id = ?
               ORDER BY created_at ASC LIMIT ?""",
            (now_s, panel_id, limit),
        )
    else:
        scoped = ", ".join("?" for _ in PANEL_SCOPED_TRIGGERS)
        cursor = await db.execute(
            f"""SELECT id, user_id, message, trigger_type, expires_at FROM voice_announcements
               WHERE delivered_at IS NULL AND expired = 0 AND expires_at > ?
                 AND (COALESCE(trigger_type, '') NOT IN ({scoped}) OR panel_id = ?)
               ORDER BY created_at ASC LIMIT ?""",
            (now_s, *PANEL_SCOPED_TRIGGERS, panel_id, limit),
        )
    rows = await cursor.fetchall()

    # 3) Atomic per-row claim — the poll-overlap guard.
    claimed: list[dict] = []
    for row in rows:
        # The 07:30 brief vs the first-turn brief (ZOE_BRIEF_ON_FIRST_TURN): never
        # play a brief the conversation already delivered, and hold it while a
        # conversational brief is mid-reply. A no-op with that flag off.
        if (row["trigger_type"] or "") == SCHEDULED_BRIEF_TRIGGER:
            import brief_first_turn

            verdict = await brief_first_turn.scheduled_row_gate(db, str(row["user_id"] or ""))
            if verdict == "defer":
                continue  # stays pending; its TTL keeps counting
            if verdict == "suppress":
                await db.execute(
                    "UPDATE voice_announcements SET expired = 1 WHERE id = ? AND delivered_at IS NULL",
                    (row["id"],),
                )
                log.info("voice_announce: %s suppressed — the first-turn brief already "
                         "delivered today's brief", row["id"])
                continue
        async with db.execute(
            """UPDATE voice_announcements SET delivered_at = ?, delivered_to = ?
               WHERE id = ? AND delivered_at IS NULL""",
            (now_s, panel_id, row["id"]),
        ) as cur:
            if getattr(cur, "rowcount", 0) != 1:
                continue  # another poller won this row
        claimed.append(
            {
                "id": row["id"],
                "text": row["message"],
                "trigger_type": row["trigger_type"] or "",
                "expires_in_s": round(_expires_in_s(row["expires_at"], now), 1),
            }
        )
    await db.commit()
    if claimed:
        log.info(
            "voice_announce: claimed %d announcement(s) for panel=%s",
            len(claimed), panel_id,
        )
    return claimed


async def mark_played(db, *, announcement_id: str, panel_id: str) -> bool:
    """Record the daemon's playback ACK; True when this call set ``played_at``.

    Only the panel that claimed the row may ACK it (``delivered_to``), and only
    once. The claim alone (``delivered_at``) is not proof of playback: the TTS
    fetch can fail, a deferred row can outlive its TTL on the daemon, or the
    daemon can stop between polling and playing.
    """
    async with db.execute(
        """UPDATE voice_announcements SET played_at = ?
           WHERE id = ? AND delivered_to = ? AND delivered_at IS NOT NULL
             AND played_at IS NULL""",
        (_fmt(_now()), announcement_id, panel_id),
    ) as cur:
        updated = getattr(cur, "rowcount", 0) == 1
    await db.commit()
    return updated
