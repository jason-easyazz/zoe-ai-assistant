"""
database.py — Database layer for zoe-data.

PostgreSQL via asyncpg (Phase 4 migration complete).
Schema is managed by Alembic; init_db() calls init_pool() and runs
alembic upgrade head on startup to ensure schema is current.
The legacy SQLite SCHEMA string is kept as a reference comment only.
"""
import json
import os
import uuid
from contextlib import asynccontextmanager

from db_pool import get_db, get_db_ctx, init_pool, close_pool  # noqa: F401 — re-exported

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Keep DB_PATH for scripts/migrations that still need the SQLite file path
# (e.g. migrate_sqlite_to_postgres.py). Not used at runtime by the app.
DB_PATH = os.environ.get("ZOE_DATA_DB", os.path.join(_BASE_DIR, "data", "zoe.db"))

POSTGRES_URL = os.environ.get("POSTGRES_URL", "")


async def log_music_event(
    user_id: str,
    event_type: str,
    query: str = "",
    track_title: str = "",
    artist: str = "",
    album: str = "",
    genre: str = "",
    source: str = "",
    volume_level=None,
    session_id: str = "",
    percent_played=None,
    duration_seconds=None,
) -> None:
    """Record a music playback event for taste learning. Never raises."""
    import time as _time
    try:
        async with get_db_ctx() as db:
            await db.execute(
                """INSERT INTO music_listening_events
                   (user_id, event_type, track_title, artist, album, genre,
                    source, query, volume_level, session_id, ts,
                    percent_played, duration_seconds)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)""",
                user_id, event_type, track_title, artist, album, genre,
                source, query, volume_level, session_id, _time.time(),
                percent_played, duration_seconds,
            )
    except Exception:
        pass  # logging must never crash a music command


async def init_db():
    """Initialize the PostgreSQL connection pool and ensure schema is current.

    Schema is managed by Alembic migrations (alembic/versions/). This function
    runs `alembic upgrade head` to bring the database to the latest revision.
    Seed data (default user, capability matrix, people field definitions) is
    inserted with ON CONFLICT DO NOTHING for idempotency.
    """
    # Guard: warn if the stale root-level zoe.db still exists (it should be archived)
    _stale = os.path.join(_BASE_DIR, "zoe.db")
    if os.path.exists(_stale):
        import logging as _log
        _log.getLogger(__name__).warning(
            "Stale SQLite file detected at %s — this file is no longer the active "
            "database (PostgreSQL is). Archive it to docs/archive/ to suppress this warning.",
            _stale,
        )

    # Initialise the asyncpg connection pool
    await init_pool()

    # Schema is managed by Alembic; run `alembic upgrade head` as a separate
    # deployment step (see deploy/migrate.sh), NOT at service startup.
    # This avoids psycopg2/asyncio thread executor deadlocks on startup.

    # Seed data — idempotent via ON CONFLICT DO NOTHING
    async with get_db_ctx() as db:
        await db.execute(
            "INSERT INTO users (id, name, role) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            "family-admin", "Admin", "admin",
        )
        try:
            from guest_policy import default_capability_matrix
            for _role, _matrix in default_capability_matrix().items():
                await db.execute(
                    """INSERT INTO role_capability_matrix (role, matrix_json)
                       VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                    _role, json.dumps(_matrix),
                )
        except Exception:
            pass

        default_fields = [
            ("nickname", "Nickname", "text", 0, None, "person", 10, "family"),
            ("pronouns", "Pronouns", "text", 0, None, "person", 20, "family"),
            ("address", "Address", "text", 0, None, "person", 30, "family"),
            ("company", "Company", "text", 0, None, "person", 40, "family"),
            ("job_title", "Job Title", "text", 0, None, "person", 50, "family"),
            ("social_handle", "Social Handle", "text", 0, None, "person", 60, "family"),
            ("important_dates", "Important Dates", "json", 0, None, "person", 70, "personal"),
            ("gift_preferences", "Gift Preferences", "json", 0, None, "person", 80, "personal"),
            ("communication_style", "Communication Style", "text", 0, None, "person", 90, "personal"),
            ("tags", "Tags", "array", 0, None, "person", 100, "family"),
        ]
        for item in default_fields:
            await db.execute(
                """INSERT INTO people_field_definitions
                   (id, field_key, label, field_type, required, options_json, scope, sort_order, visibility)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) ON CONFLICT DO NOTHING""",
                str(uuid.uuid4()), *item,
            )


# ──────────────────────────────────────────────────────────────────────────────
# Legacy SQLite SCHEMA kept as reference for migration scripts only.
# Not used at runtime. Do not add new tables here — use Alembic migrations.
# ──────────────────────────────────────────────────────────────────────────────
SCHEMA = """
-- LEGACY SQLITE SCHEMA — for reference only. Runtime uses PostgreSQL via Alembic.
-- See alembic/versions/0001_initial_schema.py for the live PostgreSQL DDL.
"""
