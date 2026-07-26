"""P-W2.3 — the daemon-facing spoken-announcement queue (voice_announce.py +
GET /api/voice/announcements).

Why this queue exists: W2's spoken morning brief "succeeded" twice with no
audio — the kiosk browser (a guest session) fire-and-forget fetched
/api/voice/speak, got a 401, and swallowed it. The Pi voice daemon (device
token) is the real speaker; this queue is its server→daemon lane.

Falsifiable pins:
  * drop the atomic rowcount-checked claim and
    test_claim_race_returns_row_exactly_once goes red;
  * return expired rows (or forget to mark them) and the TTL tests go red;
  * loosen the endpoint to sessions/guests and the auth tests go red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import inspect
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient

import voice_announce
from auth import get_current_user
from database import get_db
import routers.voice_tts as voice_tts


_SCHEMA = """CREATE TABLE voice_announcements (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    panel_id TEXT,
    message TEXT NOT NULL,
    trigger_type TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    delivered_at TEXT,
    delivered_to TEXT,
    expired INTEGER NOT NULL DEFAULT 0
)"""


@pytest.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute(_SCHEMA)
    await conn.commit()
    try:
        yield conn
    finally:
        await conn.close()


async def _rows(db):
    cur = await db.execute(
        "SELECT id, message, delivered_at, delivered_to, expired FROM voice_announcements"
    )
    return await cur.fetchall()


# ── enqueue → claim: exactly once ───────────────────────────────────────────

async def test_enqueue_then_claim_marks_delivered_exactly_once(db):
    ann_id = await voice_announce.enqueue_announcement(
        db, user_id="jason", panel_id="panel-kitchen",
        message="Good morning Jason — two things on today.",
        trigger_type="morning_checkin",
    )
    first = await voice_announce.claim_announcements(db, panel_id="zoe-touch-pi")
    assert [a["id"] for a in first] == [ann_id]
    assert first[0]["text"].startswith("Good morning")
    assert first[0]["trigger_type"] == "morning_checkin"
    assert first[0]["expires_in_s"] > 0

    # A second poll (overlap / next cycle) must never return it again.
    second = await voice_announce.claim_announcements(db, panel_id="zoe-touch-pi")
    assert second == []

    rows = await _rows(db)
    assert rows[0]["delivered_at"] is not None
    assert rows[0]["delivered_to"] == "zoe-touch-pi"
    assert rows[0]["expired"] == 0


async def test_claim_race_returns_row_exactly_once(db, monkeypatch):
    """Poll-overlap guard: a row delivered BETWEEN the candidate SELECT and the
    claim UPDATE (a concurrent poller winning the race) must be skipped —
    the rowcount check is the only thing standing between overlap and
    double-speak."""
    ann_id = await voice_announce.enqueue_announcement(
        db, user_id="jason", message="race me", panel_id="p1"
    )

    real_execute = db.execute
    state = {"raced": False}

    class _RacingCM:
        """Async-CM wrapper: the 'other poller' claims the row just before OUR
        claim UPDATE runs (voice_announce uses `async with db.execute(...)`)."""

        def __init__(self, sql, params):
            self._sql, self._params = sql, params

        async def __aenter__(self):
            await real_execute(
                "UPDATE voice_announcements SET delivered_at = 'raced', "
                "delivered_to = 'other-panel' WHERE id = ?",
                (ann_id,),
            )
            return await real_execute(self._sql, self._params)

        async def __aexit__(self, *_):
            return False

    def racing_execute(sql, params=()):
        if "SET delivered_at" in sql and not state["raced"]:
            state["raced"] = True
            return _RacingCM(sql, params)
        return real_execute(sql, params)

    monkeypatch.setattr(db, "execute", racing_execute)
    claimed = await voice_announce.claim_announcements(db, panel_id="zoe-touch-pi")
    assert claimed == [], "a row another poller claimed mid-race must NOT be returned"
    rows = await _rows(db)
    assert rows[0]["delivered_to"] == "other-panel", "the race winner keeps the claim"


async def test_claim_returns_oldest_first(db):
    a = await voice_announce.enqueue_announcement(db, user_id="u", message="first")
    b = await voice_announce.enqueue_announcement(db, user_id="u", message="second")
    # Force deterministic created_at ordering (second-resolution timestamps).
    await db.execute(
        "UPDATE voice_announcements SET created_at = '2026-01-01T06:00:00Z' WHERE id = ?", (a,)
    )
    await db.execute(
        "UPDATE voice_announcements SET created_at = '2026-01-01T07:00:00Z' WHERE id = ?", (b,)
    )
    claimed = await voice_announce.claim_announcements(db, panel_id="p")
    assert [c["id"] for c in claimed] == [a, b]


# ── TTL: expired is marked, never returned, never played ────────────────────

async def test_expired_rows_never_returned_and_marked(db, monkeypatch):
    await voice_announce.enqueue_announcement(
        db, user_id="jason", message="stale good morning", ttl_s=120
    )
    # The daemon polls 10 minutes later (service was busy / down).
    later = datetime.now(timezone.utc) + timedelta(seconds=600)
    monkeypatch.setattr(voice_announce, "_now", lambda: later)

    claimed = await voice_announce.claim_announcements(db, panel_id="zoe-touch-pi")
    assert claimed == [], "a stale 'good morning' at noon must never be spoken"

    rows = await _rows(db)
    assert rows[0]["expired"] == 1, "expired must be MARKED, not left pending forever"
    assert rows[0]["delivered_at"] is None, "expired is not delivered"


async def test_fresh_row_survives_expiry_pass(db):
    await voice_announce.enqueue_announcement(db, user_id="jason", message="fresh")
    claimed = await voice_announce.claim_announcements(db, panel_id="p")
    assert len(claimed) == 1
    assert 0 < claimed[0]["expires_in_s"] <= voice_announce._ttl_s()


async def test_ttl_env_override(db, monkeypatch):
    monkeypatch.setenv("ZOE_ANNOUNCE_TTL_S", "30")
    await voice_announce.enqueue_announcement(db, user_id="u", message="short-lived")
    claimed = await voice_announce.claim_announcements(db, panel_id="p")
    assert claimed and claimed[0]["expires_in_s"] <= 30


async def test_empty_message_rejected(db):
    with pytest.raises(ValueError):
        await voice_announce.enqueue_announcement(db, user_id="u", message="   ")


# ── strict panel matching (opt-in; default is claim-any, see module doc) ────

async def test_strict_panel_filters_other_panels(db, monkeypatch):
    monkeypatch.setenv("ZOE_ANNOUNCE_STRICT_PANEL", "1")
    await voice_announce.enqueue_announcement(
        db, user_id="u", message="for the kitchen", panel_id="panel-kitchen"
    )
    assert await voice_announce.claim_announcements(db, panel_id="zoe-touch-pi") == []
    claimed = await voice_announce.claim_announcements(db, panel_id="panel-kitchen")
    assert len(claimed) == 1


async def test_default_claims_across_panel_aliases(db, monkeypatch):
    """Default (non-strict): the presence lane records the BROWSER panel id
    while the daemon holds the DEVICE token id (#817 alias mismatch) — strict
    equality would deliver nothing, silently. Claim-any is the default."""
    monkeypatch.delenv("ZOE_ANNOUNCE_STRICT_PANEL", raising=False)
    await voice_announce.enqueue_announcement(
        db, user_id="u", message="brief", panel_id="panel_a1b2c3"  # browser alias
    )
    claimed = await voice_announce.claim_announcements(db, panel_id="zoe-touch-pi")
    assert len(claimed) == 1, "alias mismatch must not strand the announcement"


# ── endpoint auth: device token only ────────────────────────────────────────

def _app(caller_overrides: dict) -> FastAPI:
    app = FastAPI()
    app.include_router(voice_tts.router)
    for dep, value in caller_overrides.items():
        app.dependency_overrides[dep] = value
    return app


class _NullDB:
    async def execute(self, *_a, **_k):  # pragma: no cover — must not be reached
        raise AssertionError("unauthorized caller must never touch the DB")


def test_guest_session_rejected_401():
    """The kiosk browser (guest, no device token) — the exact caller whose
    silent 401 caused the W2 no-audio false-success — must stay rejected."""
    app = _app({
        voice_tts._validate_device_token: lambda: None,
        get_current_user: lambda: {"user_id": "guest", "role": "guest"},
        get_db: lambda: _NullDB(),
    })
    resp = TestClient(app).get("/api/voice/announcements")
    assert resp.status_code == 401


def test_non_guest_session_rejected_403():
    """Even an authenticated non-guest session must not drain the speaker
    queue — the daemon (device token) is the sole consumer."""
    app = _app({
        voice_tts._validate_device_token: lambda: None,
        get_current_user: lambda: {"user_id": "jason", "role": "admin"},
        get_db: lambda: _NullDB(),
    })
    resp = TestClient(app).get("/api/voice/announcements")
    assert resp.status_code == 403


def test_device_token_accepted(monkeypatch):
    claimed_with = {}

    async def fake_claim(db, *, panel_id, limit=5):
        claimed_with["panel_id"] = panel_id
        return [{"id": "a1", "text": "hi", "trigger_type": "", "expires_in_s": 100.0}]

    monkeypatch.setattr(voice_announce, "claim_announcements", fake_claim)
    app = _app({
        voice_tts._validate_device_token: lambda: {
            "panel_id": "zoe-touch-pi", "user_id": "voice-daemon", "role": "voice-daemon",
        },
        get_current_user: lambda: {"user_id": "guest", "role": "guest"},
        get_db: lambda: object(),
    })
    resp = TestClient(app).get("/api/voice/announcements")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert [a["id"] for a in body["announcements"]] == ["a1"]
    assert claimed_with["panel_id"] == "zoe-touch-pi", "claim must be keyed by the token's panel"


def test_endpoint_is_wired_through_require_voice_auth():
    """Signature pin: the endpoint's auth gate IS _require_voice_auth — a new
    bespoke check would silently drop the guest rejection."""
    sig = inspect.signature(voice_tts.voice_announcements)
    dep = sig.parameters["caller"].default
    assert getattr(dep, "dependency", None) is voice_tts._require_voice_auth


# ── migration 0025: extends the chain and creates the shape this module uses ─

def test_0025_extends_the_migration_chain_single_head():
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    svc = Path(voice_announce.__file__).resolve().parent
    cfg = Config()
    cfg.set_main_option("script_location", str(svc / "alembic"))
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg2://u:p@localhost/db")
    script = ScriptDirectory.from_config(cfg)
    # The invariant is LINEARITY (exactly one head), not that 0025 is forever
    # the newest revision — pinning the head id here makes every later migration
    # fail this test for the wrong reason. 0025's own chain link is asserted
    # below, which is what this module actually depends on.
    heads = list(script.get_heads())
    assert len(heads) == 1, f"migration graph must stay linear — heads: {heads}"
    assert script.get_revision("0025").down_revision == "0024"


def test_migration_0025_creates_the_columns_this_module_queries():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text

    svc = Path(voice_announce.__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "mig_0025", svc / "alembic" / "versions" / "0025_voice_announcements.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    eng = create_engine("sqlite://")
    with eng.connect() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()  # rerun-safe (IF NOT EXISTS)
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(voice_announcements)")).fetchall()}
    assert cols == {
        "id", "user_id", "panel_id", "message", "trigger_type",
        "created_at", "expires_at", "delivered_at", "delivered_to", "expired",
    }, "the real table must match the shape enqueue/claim read and write"
