"""Shared fixtures for the named-guest tests (``test_named_guests*.py``, the forget probe's ``named_guest_turns`` row).

SQLite with the people + contacts schema and the REAL migrations 0053 / 0055 / 0071. Invented names only (the repo is public).
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import sqlite3
import uuid
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import db_compat
import enrollment_state as es
import named_guests as ng
import speaker_models
from db_pool import _Cursor, _ExecResult

SVC = Path(__file__).resolve().parents[1]
OWNER, OTHER = "member-a", "member-b"
MID = speaker_models.acting_model()
PANEL = "panel-1"
DEVICE = {"source": "device", "panel_id": PANEL, "via_relay": False, "via_tunnel": False}
BASE = """
CREATE TABLE people (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT, email TEXT, phone TEXT, birthday TEXT,
    notes TEXT, preferences TEXT, visibility TEXT NOT NULL DEFAULT 'family', deleted INTEGER DEFAULT 0,
    is_partial INTEGER NOT NULL DEFAULT 0, context TEXT, circle TEXT, how_we_met TEXT, first_met_date TEXT,
    introduced_by_person_id TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE person_important_dates (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, user_id TEXT NOT NULL, label TEXT NOT NULL,
    date_type TEXT NOT NULL DEFAULT 'birthday', month INTEGER, day INTEGER, year INTEGER, reminder_days_before INTEGER NOT NULL DEFAULT 7,
    mem_id TEXT, created_at TEXT);
CREATE TABLE speaker_profiles (id TEXT PRIMARY KEY, user_id TEXT, display_name TEXT, embedding_blob BLOB, panel_id TEXT,
    consent_at TEXT, sample_count INTEGER, model_id TEXT);
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT);
INSERT INTO users VALUES ('member-a', 'Avery', 'admin'), ('member-b', 'Blake', 'member');
"""


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)

    def execute(self, sql, params=()):
        async def _run():
            await asyncio.sleep(0)
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()

    def q(self, sql, params=()):
        return [tuple(r) for r in self.conn.execute(sql, params).fetchall()]


def _migrate(engine, fname):
    spec = importlib.util.spec_from_file_location(f"mig_{fname[:4]}", SVC / "alembic/versions" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        mod.upgrade()


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(256)
    return (v / np.linalg.norm(v)).astype(np.float32)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    db = _Sqlite(path)
    db.conn.executescript(BASE)
    engine = sa.create_engine(f"sqlite:///{path}")
    for f in ("0053_contacts_data_model.py", "0055_omi_segments_voices.py", "0071_named_guests.py"):
        _migrate(engine, f)

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setenv("ZOE_NAMED_GUESTS", "on")
    monkeypatch.setenv("ZOE_NAMED_GUEST_MEMORY", "1")

    async def audience(uid, db=None):
        return {"member-a": "adult", "member-b": "adult", "member-kid": "minor"}.get(uid, "guest")
    monkeypatch.setattr(es, "audience_of", audience)
    ng.bind_turn(None)
    yield db
    ng.bind_turn(None)


def add_guest(db, name="Quinn", owner=OWNER, vec=None, model=MID) -> str:
    """A named guest as enrolment leaves one: the member's own person + a ``channel='panel'`` print (direct rows: the enrolment
    path itself is tested in ``test_named_guest_enrol.py``)."""
    row = db.conn.execute("SELECT id FROM people WHERE user_id = ? AND name = ?", (owner, name)).fetchone()
    pid = row[0] if row else str(uuid.uuid4())
    if not row:
        db.conn.execute("INSERT INTO people (id, user_id, name, visibility, deleted) VALUES (?, ?, ?, 'personal', 0)", (pid, owner, name))
    db.conn.execute("DELETE FROM learned_voiceprints WHERE owner_user_id = ? AND people_id = ? AND channel = 'panel'", (owner, pid))
    db.conn.execute("INSERT INTO learned_voiceprints (id, owner_user_id, people_id, model_id, channel, embedding, status, created_at, "
                    "updated_at, expires_at) VALUES (?, ?, ?, ?, 'panel', ?, 'confirmed', 'x', 'x', '9999-12-31T23:59:59Z')",
                    (str(uuid.uuid4()), owner, pid, model, (vec if vec is not None else unit(5)).tobytes()))
    db.conn.commit()
    return pid


def claim(guest_id, guest_score, member_score=None, window=""):
    block = {"model_id": MID, "speech_s": 3.0, "guest": guest_id, "guest_score": guest_score}
    if member_score is not None:
        block.update(member="member-a", score=member_score, verified=None)
    p = {"speaker": block}
    if member_score is not None:
        p.update(voice_user_id="member-a", voice_score=member_score)
    if window:
        p["window"] = window
    return p
