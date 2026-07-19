"""Speaker-ID W5 server contracts: panel claim gate + consented profile sync.

Guards three contracts in ``routers/voice_tts.py`` added for on-Pi speaker
matching (the panel cosine-matches locally against a synced profile cache and
sends back only a claim):

1. ``_accept_panel_voice_claim`` — the acceptance decision is SERVER-side:
   a claim is honoured only when ``voice_score`` parses and meets
   ``ZOE_SPEAKER_ID_THRESHOLD`` (read per call). A panel can never lower the
   bar by sending a doctored payload shape.
2. ``GET /api/voice/profiles/sync`` — hands out biometric embeddings, so it
   is device-token-only (a browser session gets 403) and returns ONLY
   consented rows (``consent_at IS NOT NULL``) plus the server threshold.
3. ``POST /api/voice/enroll`` consent plumbing — ``consent: true`` stamps
   ``consent_at``; without it the row is stored but stays unmatched.

No models, no network, no live DB: the compat-DB context manager is faked, so
this runs in the slim GitHub ``ci_safe`` lane.
"""

from __future__ import annotations

import base64
import contextlib
import sys
import types

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import routers.voice_tts as voice_tts
from routers.voice_tts import _accept_panel_voice_claim, voice_profiles_sync


# ── 1. claim gate ──────────────────────────────────────────────────────────

def test_claim_accepted_at_threshold(monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.82")
    assert _accept_panel_voice_claim({"voice_user_id": "jason", "voice_score": 0.82}) == "jason"
    assert _accept_panel_voice_claim({"voice_user_id": "jason", "voice_score": 0.99}) == "jason"


def test_claim_rejected_below_threshold(monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.82")
    assert _accept_panel_voice_claim({"voice_user_id": "jason", "voice_score": 0.8199}) is None


def test_claim_threshold_is_read_per_call(monkeypatch):
    payload = {"voice_user_id": "jason", "voice_score": 0.85}
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.9")
    assert _accept_panel_voice_claim(payload) is None
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.8")
    assert _accept_panel_voice_claim(payload) == "jason"


@pytest.mark.parametrize("payload", [
    None,
    {},
    {"voice_user_id": "", "voice_score": 0.99},
    {"voice_user_id": "jason"},                        # no score at all
    {"voice_user_id": "jason", "voice_score": None},
    {"voice_user_id": "jason", "voice_score": "high"},  # unparseable score
])
def test_claim_ignored_without_valid_score(payload, monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.0")
    # Even a zero threshold must not accept a claim that carries no real score.
    assert _accept_panel_voice_claim(payload) is None


def test_unparseable_threshold_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "not-a-float")
    assert _accept_panel_voice_claim({"voice_user_id": "jason", "voice_score": 0.83}) == "jason"
    assert _accept_panel_voice_claim({"voice_user_id": "jason", "voice_score": 0.81}) is None


# ── 2. profile sync ────────────────────────────────────────────────────────

EMB_JASON = b"\x01\x02\x03\x04" * 4
EMB_ZOE = b"\x05\x06\x07\x08" * 4


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeDB:
    """Minimal compat-DB double: records queries, serves canned rows."""

    def __init__(self, rows):
        self.rows = rows
        self.queries: list[str] = []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        return _FakeCursor(self.rows)


def _install_fake_db(monkeypatch, rows):
    db = _FakeDB(rows)

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield db

    mod = types.ModuleType("db_compat")
    mod.get_compat_db = fake_ctx
    monkeypatch.setitem(sys.modules, "db_compat", mod)
    return db


@pytest.mark.asyncio
async def test_sync_rejects_browser_sessions(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await voice_profiles_sync(caller={"source": "session", "user_id": "jason", "role": "admin"})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_sync_returns_consented_profiles_and_threshold(monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.87")
    db = _install_fake_db(monkeypatch, [
        ("jason", "Jason", EMB_JASON, 3),
        ("zoe-kid", "Kiddo", EMB_ZOE, None),
    ])

    out = await voice_profiles_sync(caller={"source": "device", "panel_id": "zoe-touch-pi"})

    assert out["ok"] is True
    assert out["threshold"] == pytest.approx(0.87)
    assert [p["user_id"] for p in out["profiles"]] == ["jason", "zoe-kid"]
    assert base64.b64decode(out["profiles"][0]["embedding_base64"]) == EMB_JASON
    assert out["profiles"][1]["sample_count"] == 1  # NULL count normalises to 1
    # The consent gate must be part of the SQL itself, not post-filtering.
    assert any("consent_at IS NOT NULL" in q for q in db.queries)


# ── 3. enroll consent plumbing ─────────────────────────────────────────────

class _WriteDB(_FakeDB):
    """Execute double for the enroll path: no existing profile, records writes."""

    def __init__(self):
        super().__init__(rows=[])
        self.params: list[tuple] = []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        self.params.append(tuple(params))

        class _Cur(_FakeCursor):
            async def fetchone(self):
                return None  # no existing profile → INSERT branch

            # Write sites do `await db.execute(...)`; read sites use
            # `async with db.execute(...)`. Support both shapes.
            def __await__(self):
                async def _done():
                    return self
                return _done().__await__()

        return _Cur([])

    async def commit(self):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("consent,expect_stamp", [(True, True), (False, False)])
async def test_enroll_stamps_consent_only_when_given(monkeypatch, consent, expect_stamp):
    db = _WriteDB()

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield db

    mod = types.ModuleType("db_compat")
    mod.get_compat_db = fake_ctx
    monkeypatch.setitem(sys.modules, "db_compat", mod)
    monkeypatch.setattr(voice_tts, "_compute_resemblyzer_embedding", lambda _p: EMB_JASON)

    payload = {
        "audio_base64": base64.b64encode(b"RIFF-fake-wav").decode(),
        "user_id": "jason",
        "display_name": "Jason",
        "consent": consent,
    }
    out = await voice_tts.voice_enroll(payload, caller={"source": "device", "user_id": "voice-daemon"})

    assert out["ok"] is True
    inserts = [q for q in db.queries if "INSERT INTO speaker_profiles" in q]
    assert len(inserts) == 1
    assert ("consent_at" in inserts[0]) is expect_stamp
