"""P-F4 — ambient_memory user scoping.

Write path (`POST /api/voice/ambient`): every stored transcript is bound to the
panel's resolved user; an unresolvable panel SKIPS the insert (never store
ownerless audio). Read path (`ambient_search` MCP tool): results are always
scoped to the caller's resolved user — user A can never read user B's rows.

Slim-CI-safe: `routers.voice_tts` lazy-imports every heavy engine (see
tests/AGENTS.md voice-smoke exception) and `mcp_server` imports under slim deps
(same as test_mcp_server_import_hygiene.py). No model, network, or DB.
"""

from __future__ import annotations

import base64
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db_compat  # noqa: E402
import db_pool  # noqa: E402
import mcp_server  # noqa: E402
import routers.voice_tts as voice_tts  # noqa: E402

_AUDIO_B64 = base64.b64encode(b"\x00" * 64).decode("ascii")


class _FakeCursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return [self._row] if self._row else []


class _RecordingCompatDb:
    """Stands in for db_compat's AsyncpgCompat in the ambient write path."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return _FakeCursor()

    async def commit(self):
        pass

    @property
    def inserts(self):
        return [c for c in self.calls if "INSERT INTO ambient_memory" in c[0]]


@pytest.fixture()
def compat_db(monkeypatch):
    db = _RecordingCompatDb()

    @asynccontextmanager
    async def _fake_get_compat_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", _fake_get_compat_db)

    async def _fake_transcribe(_wav_path: str) -> str:
        return "we should order pizza on friday"

    monkeypatch.setattr(voice_tts, "_transcribe_audio_impl", _fake_transcribe)
    return db


# ── Write path ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ambient_insert_binds_resolved_panel_user(compat_db, monkeypatch):
    async def _resolve(_panel_id, _db):
        return "jason"

    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", _resolve)

    result = await voice_tts.voice_ambient(
        {"audio_base64": _AUDIO_B64, "panel_id": "zoe-touch-pi", "room": "kitchen"},
        caller={"panel_id": "zoe-touch-pi"},
    )

    assert result["ok"] is True
    assert result["stored"] is True
    assert len(compat_db.inserts) == 1
    sql, params = compat_db.inserts[0]
    assert "user_id" in sql
    assert "jason" in params


@pytest.mark.asyncio
async def test_ambient_insert_skipped_when_user_unresolvable(compat_db, monkeypatch):
    async def _resolve(_panel_id, _db):
        return None

    monkeypatch.setattr(voice_tts, "_resolve_panel_default_user", _resolve)

    result = await voice_tts.voice_ambient(
        {"audio_base64": _AUDIO_B64, "panel_id": "panel-unknown"},
        caller={"panel_id": "panel-unknown"},
    )

    # Endpoint still acks the segment, but nothing ownerless hits the table.
    assert result["ok"] is True
    assert result["stored"] is False
    assert compat_db.inserts == []


# ── Read path ────────────────────────────────────────────────────────────────

_ROWS = [
    {
        "id": 1,
        "timestamp": "2026-07-07 09:00:00",
        "panel_id": "zoe-touch-pi",
        "room": "kitchen",
        "speaker_id": None,
        "transcript": "user-a talked about pizza with brad",
        "user_id": "user-a",
    },
    {
        "id": 2,
        "timestamp": "2026-07-07 09:05:00",
        "panel_id": "zoe-touch-pi",
        "room": "kitchen",
        "speaker_id": None,
        "transcript": "user-b private pizza conversation",
        "user_id": "user-b",
    },
]


class _FakeRawConn:
    """asyncpg-shaped conn that enforces the user_id predicate like Postgres would."""

    def __init__(self, rows):
        self._rows = rows
        self.queries: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *params):
        self.queries.append((sql, params))
        match = re.search(r"m\.user_id = \$(\d+)", sql)
        assert match, "ambient_search SQL must carry a mandatory m.user_id predicate"
        scoped_user = params[int(match.group(1)) - 1]
        return [dict(r) for r in self._rows if r["user_id"] == scoped_user]


class _ActorRoleDb:
    """Minimal db for _execute_tool's actor-role lookup (role → member)."""

    async def execute(self, *_args, **_kwargs):
        return _FakeCursor(None)


@pytest.fixture()
def raw_conn(monkeypatch):
    conn = _FakeRawConn(_ROWS)

    @asynccontextmanager
    async def _fake_get_db_ctx():
        yield conn

    monkeypatch.setattr(db_pool, "get_db_ctx", _fake_get_db_ctx)
    return conn


@pytest.mark.asyncio
async def test_ambient_search_scopes_to_calling_user(raw_conn):
    result = await mcp_server._execute_tool(
        db=_ActorRoleDb(),
        name="ambient_search",
        args={"_user_id": "user-a", "query": "pizza"},
    )

    assert "error" not in result
    assert result["count"] == 1
    transcripts = [r["transcript"] for r in result["results"]]
    assert transcripts == ["user-a talked about pizza with brad"]
    # The scoping predicate is present even with zero optional filters.
    sql, params = raw_conn.queries[0]
    assert re.search(r"m\.user_id = \$\d+", sql)
    assert "user-a" in params


@pytest.mark.asyncio
async def test_ambient_search_never_returns_other_users_rows(raw_conn):
    result = await mcp_server._execute_tool(
        db=_ActorRoleDb(),
        name="ambient_search",
        args={"_user_id": "user-b", "query": "pizza"},
    )

    assert "error" not in result
    transcripts = [r["transcript"] for r in result["results"]]
    assert transcripts == ["user-b private pizza conversation"]
    assert all("user-a" not in t for t in transcripts)
