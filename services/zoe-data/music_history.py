"""music_history — Zoe's per-user listening journal (append-only, fully local).

WHY THIS EXISTS: Music Assistant's playlog cannot answer "who listened to
what": it upserts ONE row per item/user (no event timeline), prunes at 90
days, and every Zoe-initiated play collapses to the single household MA-token
user. Attribution therefore happens at ZOE's layer, in the
``music_play_history`` table (alembic 0019), which feeds per-user taste
profiles for the weekly discovery batch (music_discovery.py).

Two event sources:
  - ``initiated`` — journaled at the moment Zoe triggers playback
    (music_service.search_and_play / play_media), attributed to the acting
    user the caller resolved (skybridge threads it from the session/
    X-Zoe-User-Id identity layer).
  - ``observed`` — a light scheduled poll of MA ``music/recently_played_items``
    back-fills plays Zoe did not start (radio-mode auto-continuation, queue
    rollover), deduped by URI against recent journal rows.

USER ATTRIBUTION (multi-user by design): ``zoe_user_id`` is ALWAYS populated.
Plays that cannot be attributed to an identified user get the reserved
``GUEST_USER_ID`` ("guest" — same convention as the kiosk-guest auth model /
skybridge's guest set; it is an id constant, not a family-users row). The
fallback lives in ONE choke point, ``resolve_music_user()`` — never scatter
per-call defaults. Guest-attributed history builds the guest/household
profile and never pollutes a named member's profile. Speaker-ID attribution
for anonymous voice turns is a Samantha-roadmap follow-up, not built here.

Every helper is best-effort and never raises — music must keep playing when
the journal can't be written.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

GUEST_USER_ID = "guest"
# Ids the auth layer hands out for unidentified sessions (kiosk panel,
# unrecognized voice) — all collapse to the reserved guest id.
_GUEST_ALIASES = {"", "guest", "voice-guest"}

_OBSERVE_DEDUP_HOURS = float(os.environ.get("ZOE_MUSIC_OBSERVE_DEDUP_H", "12"))
_OBSERVE_ATTRIBUTION_MIN = float(os.environ.get("ZOE_MUSIC_OBSERVE_ATTRIB_MIN", "30"))

_table_ready = False


def resolve_music_user(user_id: Optional[str]) -> str:
    """THE attribution choke point: identified user id, else the reserved
    guest id. Every music surface (intents, routers, observers) resolves
    through here — no scattered defaults."""
    uid = (user_id or "").strip()
    return uid if uid and uid.lower() not in _GUEST_ALIASES else GUEST_USER_ID


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table(conn) -> bool:
    """True when music_play_history is usable. Existence check first — a
    least-privilege role must not issue CREATE once alembic 0019 has run."""
    global _table_ready
    if _table_ready:
        return True
    try:
        await conn.fetchval("SELECT 1 FROM music_play_history LIMIT 1")
        _table_ready = True
        return True
    except Exception:  # noqa: BLE001 — table absent (unmigrated dev/test DB)
        pass
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS music_play_history (
                id BIGSERIAL PRIMARY KEY,
                played_at TEXT NOT NULL,
                zoe_user_id TEXT NOT NULL,
                source TEXT NOT NULL,
                track TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                uri TEXT NOT NULL DEFAULT '',
                media_type TEXT NOT NULL DEFAULT '',
                player_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        _table_ready = True
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("music_play_history unavailable (journal skipped): %s", exc)
        return False


async def record_play(user_id: Optional[str], *, source: str,
                      track: str = "", artist: str = "", album: str = "",
                      provider: str = "", uri: str = "", media_type: str = "",
                      player_id: str = "",
                      played_at: Optional[str] = None) -> bool:
    """Append one play event. Never raises; False when the journal is down."""
    try:
        from db_pool import get_db_ctx
        async with get_db_ctx() as conn:
            if not await _ensure_table(conn):
                return False
            await conn.execute(
                """
                INSERT INTO music_play_history
                    (played_at, zoe_user_id, source, track, artist, album,
                     provider, uri, media_type, player_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                played_at or _now_iso(), resolve_music_user(user_id), source,
                track or "", artist or "", album or "", provider or "",
                uri or "", media_type or "", player_id or "",
            )
        return True
    except Exception as exc:  # noqa: BLE001 — journaling must never break playback
        logger.warning("music journal write failed (non-fatal): %s", exc)
        return False


def _split_artist(name: str) -> str:
    """'A, B & C' → 'A' — one clean profile name per credit string."""
    return re.split(r",| & | feat\.? | featuring ", name or "", maxsplit=1)[0].strip()


def media_fields(media: dict[str, Any]) -> dict[str, str]:
    """Normalize an MA media item (search hit / item_by_uri result) into the
    journal's columns. Best-effort — missing fields become ''."""
    artists = media.get("artists") or []
    artist = ", ".join(a.get("name", "") for a in artists if isinstance(a, dict)) \
        if isinstance(artists, list) else ""
    album = media.get("album")
    album_name = album.get("name", "") if isinstance(album, dict) else (album or "")
    return {
        "track": media.get("name") or "",
        "artist": artist or (media.get("artist") if isinstance(media.get("artist"), str) else ""),
        "album": album_name if isinstance(album_name, str) else "",
        "provider": media.get("provider") or "",
        "uri": media.get("uri") or "",
        "media_type": str(media.get("media_type") or ""),
    }


# ── Observed events: poll MA for plays Zoe didn't start ──────────────────────

async def observe_once() -> int:
    """Journal recently played MA tracks that aren't in the journal yet.

    Attribution: an observed play within ``ZOE_MUSIC_OBSERVE_ATTRIB_MIN``
    minutes of an initiated event inherits that event's user (they asked for
    the session the radio-mode continued); otherwise it is guest-attributed.
    Idempotent via a per-URI dedupe window (``ZOE_MUSIC_OBSERVE_DEDUP_H``).
    Quiet when MA is down: one log line, returns 0. Never raises.
    """
    try:
        import music_service as ms

        recent = await ms._ma(
            "music/recently_played_items",
            limit=30, media_types=["track"], fully_played_only=False,
        )
        if recent is None:
            logger.info("music observe: MA unreachable, skipping this pass")
            return 0

        from db_pool import get_db_ctx
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=_OBSERVE_DEDUP_HOURS)).isoformat()
        attrib_cutoff = (datetime.now(timezone.utc)
                         - timedelta(minutes=_OBSERVE_ATTRIBUTION_MIN)).isoformat()
        inserted = 0
        async with get_db_ctx() as conn:
            if not await _ensure_table(conn):
                return 0
            last_user = await conn.fetchval(
                "SELECT zoe_user_id FROM music_play_history "
                "WHERE source = 'initiated' AND played_at >= $1 "
                "ORDER BY played_at DESC LIMIT 1", attrib_cutoff)
            for item in recent if isinstance(recent, list) else []:
                uri = item.get("uri") if isinstance(item, dict) else None
                if not uri:
                    continue
                known = await conn.fetchval(
                    "SELECT 1 FROM music_play_history "
                    "WHERE uri = $1 AND played_at >= $2 LIMIT 1", uri, cutoff)
                if known:
                    continue
                full = await ms._ma("music/item_by_uri", uri=uri)
                fields = media_fields(full if isinstance(full, dict) else dict(item))
                fields["uri"] = uri
                await conn.execute(
                    """
                    INSERT INTO music_play_history
                        (played_at, zoe_user_id, source, track, artist, album,
                         provider, uri, media_type, player_id)
                    VALUES ($1, $2, 'observed', $3, $4, $5, $6, $7, $8, '')
                    """,
                    _now_iso(), resolve_music_user(last_user),
                    fields["track"], fields["artist"], fields["album"],
                    fields["provider"], fields["uri"], fields["media_type"],
                )
                inserted += 1
        if inserted:
            logger.info("music observe: journaled %d play(s)", inserted)
        return inserted
    except Exception as exc:  # noqa: BLE001
        logger.warning("music observe pass failed (non-fatal): %s", exc)
        return 0


# ── Taste profile reads ──────────────────────────────────────────────────────

async def top_artists(user_id: Optional[str] = None, days: int = 90,
                      limit: int = 10) -> list[str]:
    """Most-played artist names from the journal, first-credit collapsed.

    ``user_id=None`` → the whole-household aggregate (every user incl. guest,
    for the shared discovery playlist). A specific id — including the reserved
    ``GUEST_USER_ID`` — returns ONLY that user's rows, so guest plays never
    pollute a named member's profile. Never raises; [] on any failure.
    """
    try:
        from db_pool import get_db_ctx
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with get_db_ctx() as conn:
            if not await _ensure_table(conn):
                return []
            if user_id is None:
                rows = await conn.fetch(
                    "SELECT artist FROM music_play_history "
                    "WHERE played_at >= $1 AND artist <> ''", since)
            else:
                rows = await conn.fetch(
                    "SELECT artist FROM music_play_history "
                    "WHERE played_at >= $1 AND artist <> '' AND zoe_user_id = $2",
                    since, resolve_music_user(user_id))
        counts: dict[str, int] = {}
        order: list[str] = []
        for r in rows:
            name = _split_artist(r["artist"])
            if not name:
                continue
            if name not in counts:
                order.append(name)
            counts[name] = counts.get(name, 0) + 1
        return sorted(order, key=lambda n: -counts[n])[:limit]
    except Exception as exc:  # noqa: BLE001
        logger.warning("music top_artists failed (non-fatal): %s", exc)
        return []
