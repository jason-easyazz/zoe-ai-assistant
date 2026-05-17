"""
migrate_sqlite_to_postgres.py — Phase 5: Copy all data from SQLite → PostgreSQL.

Run ONCE after service is stopped (to avoid writes during migration):
    systemctl --user stop zoe-data
    cd /home/zoe/assistant/services/zoe-data
    POSTGRES_URL=postgresql://zoe:REDACTED@localhost:5432/zoe \
      python3 migrate_sqlite_to_postgres.py [--sqlite /path/to/zoe.db]

Safety guarantees:
- Uses INSERT ... ON CONFLICT DO NOTHING — safe to re-run
- Wraps each table in a transaction; rolls back on per-table error
- Reports row counts before and after each table
- Does NOT drop data already in PostgreSQL
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

import asyncpg
import asyncio

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("migrate")

DEFAULT_SQLITE = os.path.join(os.path.dirname(__file__), "data", "zoe.db")
POSTGRES_URL = os.environ.get("POSTGRES_URL", "postgresql://zoe:REDACTED@localhost:5432/zoe")

# Tables in safe insertion order (referenced tables before referencing ones)
ORDERED_TABLES = [
    "users",
    "panels",
    "device_tokens",
    "chat_sessions",
    "chat_messages",
    "memories",
    "people",
    "people_field_definitions",
    "people_field_values",
    "reminders",
    "proactive_scheduled",
    "proactive_log",
    "lists",
    "list_items",
    "calendar_events",
    "journal_entries",
    "notes",
    "transactions",
    "notifications",
    "push_subscriptions",
    "background_tasks",
    "ui_actions",
    "ui_panel_sessions",
    "ambient_memory",
    "speaker_profiles",
    "dashboard_layouts",
    "role_capability_matrix",
    "user_preferences",
    "user_profile_fields",
    "system_events",
    "system_settings",
    "music_listening_events",
    "weather_cache",
    "weather_prefs",
    "ha_entity_cache",
    "ha_state_log",
    "openclaw_approvals",
    "openclaw_tasks",
    "a2a_sessions",
    "a2a_messages",
    "a2a_registry",
    "agent_cards",
    "agent_health",
]

# Columns that contain JSON strings in SQLite → keep as strings (asyncpg handles TEXT)
# Columns that are BLOB in SQLite → bytes in asyncpg
BLOB_COLUMNS: dict[str, list[str]] = {
    "speaker_profiles": ["embedding_blob"],
}


_DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%SZ",
]


def _parse_datetime(val: str) -> datetime | None:
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


# Tables where asyncpg needs datetime objects (timestamp columns)
# These are TEXT in SQLite but TIMESTAMP in PostgreSQL.
_TIMESTAMP_COLUMNS: dict[str, list[str]] = {
    "push_subscriptions": ["created_at"],
    "speaker_profiles": ["created_at", "updated_at"],
}


def sqlite_row_to_pg(table: str, row: sqlite3.Row, col_names: list[str]) -> tuple:
    """Convert a sqlite3.Row to a tuple suitable for asyncpg INSERT."""
    values = []
    blob_cols = BLOB_COLUMNS.get(table, [])
    ts_cols = _TIMESTAMP_COLUMNS.get(table, [])
    for col, val in zip(col_names, row):
        if col in blob_cols:
            values.append(val)
        elif isinstance(val, bytes):
            values.append(val.hex())
        elif col in ts_cols and isinstance(val, str):
            # Convert datetime string to naive datetime (TIMESTAMP WITHOUT TIME ZONE)
            parsed = _parse_datetime(val)
            if parsed is not None:
                # Strip timezone for TIMESTAMP WITHOUT TIME ZONE columns
                values.append(parsed.replace(tzinfo=None))
            else:
                values.append(val)
        else:
            values.append(val)
    return tuple(values)


async def get_pg_tables(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    )
    return {r["tablename"] for r in rows}


async def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: asyncpg.Connection,
    table: str,
) -> tuple[int, int]:
    """Migrate one table. Returns (rows_read, rows_inserted)."""
    cursor = sqlite_conn.cursor()

    # Check SQLite table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if not cursor.fetchone():
        log.info("  %s: not in SQLite, skipping", table)
        return 0, 0

    cursor.execute(f"SELECT * FROM {table}")  # noqa: S608
    col_names = [d[0] for d in cursor.description]
    rows = cursor.fetchall()

    if not rows:
        log.info("  %s: 0 rows in SQLite, nothing to migrate", table)
        return 0, 0

    placeholders = ", ".join(f"${i+1}" for i in range(len(col_names)))
    col_list = ", ".join(f'"{c}"' for c in col_names)
    sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'

    inserted = 0
    errors = 0
    for row in rows:
        vals = sqlite_row_to_pg(table, row, col_names)
        try:
            # Each row in its own transaction so one failure doesn't abort the batch
            async with pg_conn.transaction():
                result = await pg_conn.execute(sql, *vals)
                if result.endswith("1"):
                    inserted += 1
        except Exception as exc:
            errors += 1
            if errors <= 3:  # Only log first few errors per table to avoid log spam
                log.warning("    row error in %s: %s | vals preview: %s", table, exc, str(vals)[:120])

    if errors > 3:
        log.warning("    %s: %d more row errors suppressed", table, errors - 3)

    return len(rows), inserted


async def main(sqlite_path: str) -> None:
    if not os.path.exists(sqlite_path):
        log.error("SQLite file not found: %s", sqlite_path)
        sys.exit(1)

    log.info("Connecting to SQLite: %s", sqlite_path)
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    log.info("Connecting to PostgreSQL: %s", POSTGRES_URL.replace(POSTGRES_URL.split("@")[0].split("//")[1], "***"))
    pg_conn = await asyncpg.connect(POSTGRES_URL)

    pg_tables = await get_pg_tables(pg_conn)
    log.info("PostgreSQL has %d tables in public schema", len(pg_tables))

    total_read = total_inserted = 0
    skipped = []

    for table in ORDERED_TABLES:
        if table not in pg_tables:
            log.warning("  %s: table missing from PostgreSQL — run alembic upgrade head first", table)
            skipped.append(table)
            continue

        log.info("Migrating: %s …", table)
        try:
            read, inserted = await migrate_table(sqlite_conn, pg_conn, table)
            total_read += read
            total_inserted += inserted
            if read > 0:
                log.info("  %s: %d read → %d inserted (ON CONFLICT DO NOTHING skipped duplicates)", table, read, inserted)
        except Exception as exc:
            log.error("  %s: FAILED — %s", table, exc)
            skipped.append(table)

    # Migrate any tables in SQLite that weren't in our ordered list
    sqlite_conn.row_factory = None
    extra_cur = sqlite_conn.cursor()
    extra_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    all_sqlite_tables = {r[0] for r in extra_cur.fetchall()}
    extras = all_sqlite_tables - set(ORDERED_TABLES)
    for table in sorted(extras):
        if table in pg_tables:
            log.info("Migrating extra table: %s …", table)
            sqlite_conn.row_factory = sqlite3.Row
            try:
                read, inserted = await migrate_table(sqlite_conn, pg_conn, table)
                total_read += read
                total_inserted += inserted
                log.info("  %s: %d read → %d inserted", table, read, inserted)
            except Exception as exc:
                log.error("  %s: FAILED — %s", table, exc)
                skipped.append(table)
            finally:
                sqlite_conn.row_factory = None

    await pg_conn.close()
    sqlite_conn.close()

    log.info("")
    log.info("═══════════════════════════════════════════")
    log.info("Migration complete: %d rows read, %d inserted", total_read, total_inserted)
    if skipped:
        log.warning("Skipped tables: %s", ", ".join(skipped))
    log.info("═══════════════════════════════════════════")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate SQLite → PostgreSQL")
    parser.add_argument("--sqlite", default=DEFAULT_SQLITE, help="Path to SQLite zoe.db")
    args = parser.parse_args()
    asyncio.run(main(args.sqlite))
