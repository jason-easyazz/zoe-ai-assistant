"""P-F4 — ambient_memory user scoping.

Write path (`POST /api/voice/ambient`): a transcript is stored only under the
segment's OWN identification (an accepted, consented voice claim, or the caller's
own signed-in session); an unidentified speaker SKIPS the insert - never filed
under the panel's member and never under the shared guest scope (2026-10-14). Read path (`ambient_search` MCP tool): results are always
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

R34 = "wespeaker_resnet34+i8qdq-u8s8+c1"      # the only speaker model the server acts on (Resemblyzer was retired)

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


def _ts(age_s: float) -> str:
    """A ``ui_panel_sessions.last_seen_at`` written ``age_s`` seconds ago (Postgres TEXT shape)."""
    return (datetime.now(timezone.utc) - timedelta(seconds=age_s)).strftime("%Y-%m-%d %H:%M:%S.%f+00")


class _RecordingCompatDb:
    """Stands in for every DB the ambient write path could consult.

    It answers the panel-identity lookups a member-attributing write path would make, so a
    regression that files ambient audio under the panel's member is CAUGHT rather than masked
    by an empty table: ``ui_panel_sessions`` (``session_user`` signed in ``session_age_s`` ago)
    and ``panel_user_bindings`` (``owner``, the operator-declared default). It records inserts.
    """

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.session_user: str | None = None
        self.session_age_s: float = 30.0
        self.owner: str | None = None

    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        u = " ".join(sql.split()).upper()
        if "FROM UI_PANEL_SESSIONS" in u:
            return _FakeCursor({"user_id": self.session_user, "last_seen_at": _ts(self.session_age_s)}
                               if self.session_user else None)
        if "FROM PANEL_USER_BINDINGS" in u:
            return _FakeCursor({"user_id": self.owner} if self.owner else None)
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
    # The voice lane's fresh-identity resolver reads the same store on a dedicated pool connection.
    monkeypatch.setattr(db_pool, "get_db_ctx", _fake_get_compat_db)

    async def _idle_window():
        return 900

    monkeypatch.setattr(voice_tts, "_panel_idle_logout_s", _idle_window)

    async def _fake_transcribe(_wav_path: str) -> str:
        return "we should order pizza on friday"

    monkeypatch.setattr(voice_tts, "_transcribe_audio_impl", _fake_transcribe)
    return db


def _consent(monkeypatch, consented: bool = True):
    async def _consented(_uid, *_a):
        return consented

    monkeypatch.setattr(voice_tts, "_voice_claim_consented", _consented)


DEVICE = {"source": "device", "user_id": "voice-daemon", "panel_id": "zoe-touch-pi"}


async def _post(payload: dict, caller: dict):
    body = {"audio_base64": _AUDIO_B64, "panel_id": "zoe-touch-pi", "room": "kitchen", **payload}
    return await voice_tts.voice_ambient(body, caller=dict(caller))


# ── Write path ───────────────────────────────────────────────────────────────
# PRIVACY (2026-10-14): ambient audio is overheard ROOM speech, so the panel's signed-in member
# is not its speaker. A segment is filed only under ITS OWN identification (a voice claim that
# passes the server's gate + consent, or the caller's own signed-in session); an unidentified
# segment is NOT FILED - not under a member, and not under "guest" (a shared scope any guest
# session's ambient_search would read). P-F4: ownerless ambient audio is never stored.

@pytest.mark.asyncio
@pytest.mark.parametrize("session_user,session_age_s,owner", [
    ("jason", 30, None),            # a FRESH PIN session on the panel
    ("jason", 6 * 3600, None),      # the panel's LAST signed-in member (stale)
    (None, 0, "jason"),             # the operator-declared panel owner
    ("jason", 30, "jason"),         # all of them at once
], ids=["fresh_pin_session", "last_signed_in", "declared_owner", "all"])
async def test_unidentified_ambient_is_never_filed_under_a_member(compat_db, session_user, session_age_s, owner):
    compat_db.session_user, compat_db.session_age_s, compat_db.owner = session_user, session_age_s, owner

    result = await _post({}, DEVICE)

    assert result["ok"] is True                     # the segment is still acked
    assert result["stored"] is False
    assert compat_db.inserts == [], "an unidentified speaker's room audio was filed: %r" % (compat_db.inserts,)


@pytest.mark.asyncio
async def test_unidentified_ambient_on_an_unknown_panel_is_not_filed_as_guest(compat_db):
    result = await _post({"panel_id": "panel-unknown"}, DEVICE)

    assert result["stored"] is False
    assert compat_db.inserts == []


@pytest.mark.asyncio
async def test_ambient_with_an_accepted_voice_claim_is_filed_under_that_member(compat_db, monkeypatch):
    """NEGATIVE CONTROL: the same rig DOES file a segment whose speaker identified itself - under
    the claimed member, never the panel's signed-in one."""
    _consent(monkeypatch)
    compat_db.session_user, compat_db.owner = "jason", "jason"

    result = await _post({"voice_user_id": "user-b", "voice_score": 0.99, "voice_model_id": R34}, DEVICE)

    assert result["stored"] is True
    assert len(compat_db.inserts) == 1
    sql, params = compat_db.inserts[0]
    assert "user_id" in sql
    assert params[-1] == "user-b"
    assert "jason" not in params


@pytest.mark.asyncio
@pytest.mark.parametrize("score,consented", [(0.10, True), (0.99, False)], ids=["below_threshold", "consent_revoked"])
async def test_ambient_with_a_refused_claim_is_not_filed(compat_db, monkeypatch, score, consented):
    _consent(monkeypatch, consented)
    compat_db.session_user = "jason"

    result = await _post({"voice_user_id": "user-b", "voice_score": score, "voice_model_id": R34}, DEVICE)

    assert result["stored"] is False
    assert compat_db.inserts == []


@pytest.mark.asyncio
async def test_ambient_raw_identified_user_id_is_not_an_identification(compat_db):
    """A bare ``identified_user_id`` is an assertion, not a verdict: it passed no server gate."""
    result = await _post({"identified_user_id": "user-b"}, DEVICE)

    assert result["stored"] is False
    assert compat_db.inserts == []


@pytest.mark.asyncio
async def test_ambient_from_a_members_own_session_is_filed_under_them(compat_db, monkeypatch):
    """NEGATIVE CONTROL: the caller's own signed-in session is a fresh source. A session caller
    cannot claim somebody else's voice (claims are device-token only)."""
    _consent(monkeypatch)
    compat_db.session_user = "jason"

    result = await _post({"voice_user_id": "jason", "voice_score": 0.99, "voice_model_id": R34},
                         {"source": "session", "user_id": "user-b", "role": "user"})

    assert result["stored"] is True
    assert compat_db.inserts[0][1][-1] == "user-b"


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
