"""Module-local SQLite database for Orbit."""
from __future__ import annotations
import aiosqlite
import json
import os
from pathlib import Path
from contextlib import asynccontextmanager

DB_PATH = os.environ.get("ORBIT_DB", str(Path(__file__).parent / "data" / "orbit.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    venue_name TEXT NOT NULL,
    event_name TEXT,
    zone_names TEXT DEFAULT '{}',
    join_code TEXT,
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS checkins (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    intent TEXT NOT NULL,
    intents TEXT DEFAULT '[]',
    desires TEXT DEFAULT '[]',
    visibility TEXT DEFAULT 'public',
    interests TEXT DEFAULT '[]',
    interest_intensity TEXT DEFAULT '{}',
    top_values TEXT DEFAULT '[]',
    personality TEXT,
    activities TEXT DEFAULT '[]',
    group_size INTEGER DEFAULT 1,
    zone TEXT,
    checked_out INTEGER DEFAULT 0,
    met_by TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    checkin_a TEXT NOT NULL,
    checkin_b TEXT NOT NULL,
    score REAL NOT NULL,
    icebreaker TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS interactions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    receiver_id TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT,
    status TEXT DEFAULT 'sent',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    scanner_id TEXT NOT NULL,
    scanned_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    is_mutual INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    responded_at TEXT
);

CREATE TABLE IF NOT EXISTS contact_exchanges (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    contact_data TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS safety_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    reporter_id TEXT NOT NULL,
    reported_id TEXT NOT NULL,
    type TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_challenges (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    prize_text TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS challenge_scores (
    id TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL,
    checkin_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    scans INTEGER DEFAULT 0,
    correct INTEGER DEFAULT 0,
    points INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS speed_dating_sessions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    round_duration_seconds INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    active INTEGER DEFAULT 1
);
"""


@asynccontextmanager
async def get_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


async def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    await migrate_db()


def row_to_dict(row: aiosqlite.Row) -> dict:
    return dict(row)


def parse_json_fields(d: dict, fields: list[str]) -> dict:
    for f in fields:
        if f in d and isinstance(d[f], str):
            try:
                d[f] = json.loads(d[f])
            except (json.JSONDecodeError, TypeError):
                d[f] = {} if f.endswith(("intensity", "names", "personality")) else []
    # Normalise top_values → values for internal use
    if "top_values" in d and "values" not in d:
        d["values"] = d.pop("top_values")
    return d


async def migrate_db() -> None:
    """Add new columns to existing databases that predate schema additions."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # Add intents + desires columns if missing
        for col, default in [("intents", "'[]'"), ("desires", "'[]'")]:
            try:
                await db.execute(f"ALTER TABLE checkins ADD COLUMN {col} TEXT DEFAULT {default}")
                await db.commit()
            except Exception:
                pass  # column already exists
        # Add join_code to sessions if missing
        try:
            await db.execute("ALTER TABLE sessions ADD COLUMN join_code TEXT")
            await db.commit()
        except Exception:
            pass  # column already exists
