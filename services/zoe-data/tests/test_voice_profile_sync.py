"""Speaker-ID W5 server contracts: panel claim gate + consented profile sync.

Guards three contracts in ``routers/voice_tts.py`` added for on-Pi speaker
matching (the panel cosine-matches locally against a synced profile cache and
sends back only a claim):

1. ``_accept_panel_voice_claim`` — the acceptance decision is SERVER-side:
   a claim is honoured only when it names the acting model (ResNet34; the retired
   Resemblyzer is never honoured), ``voice_score`` parses and meets that model's
   length-group bar (``ZOE_SPEAKER_RESNET34_THRESHOLD_*``, read per call). A panel
   can never lower the bar by sending a doctored payload shape.
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

import numpy as np
import pytest

from speaker_models import RESNET34_MODEL_ID as R34

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import routers.voice_tts as voice_tts
from routers.voice_tts import _accept_panel_voice_claim, voice_profiles_sync


# ── 1. claim gate ──────────────────────────────────────────────────────────

DEVICE_CALLER = {"source": "device", "panel_id": "zoe-touch-pi", "user_id": "voice-daemon", "via_relay": False, "via_tunnel": False}


def _claim(score, user="jason", **extra):
    """A panel claim in the acting model's space, on a clip longer than 2 s (the 0.265 bar)."""
    return {"voice_user_id": user, "voice_score": score, "voice_model_id": R34, "voice_speech_s": 3.0, **extra}


@pytest.fixture(autouse=True)
def _default_resnet34_bars(monkeypatch):
    for k in ("TIER", "THRESHOLD_SHORT", "THRESHOLD_LONG"):
        monkeypatch.delenv("ZOE_SPEAKER_RESNET34_" + k, raising=False)
    monkeypatch.delenv("ZOE_SPEAKER_MODEL", raising=False)


def test_claim_accepted_at_threshold():
    assert _accept_panel_voice_claim(_claim(0.265), DEVICE_CALLER) == "jason"
    assert _accept_panel_voice_claim(_claim(0.99), DEVICE_CALLER) == "jason"


def test_claim_rejected_below_threshold():
    assert _accept_panel_voice_claim(_claim(0.2649), DEVICE_CALLER) is None


def test_a_claim_naming_no_model_or_the_retired_one_is_never_honoured():
    assert _accept_panel_voice_claim({"voice_user_id": "jason", "voice_score": 0.99}, DEVICE_CALLER) is None
    assert _accept_panel_voice_claim(_claim(0.99, voice_model_id="resemblyzer"), DEVICE_CALLER) is None


@pytest.mark.parametrize("caller", [
    None,
    {},
    {"source": "session", "user_id": "jason", "role": "admin"},  # logged-in browser
    {"source": "session", "user_id": "mallory", "role": "member"},
])
def test_claim_rejected_for_non_device_callers(monkeypatch, caller):
    # Security invariant: only device-token callers may claim a speaker
    # identity — a browser session with a perfect score is still ignored.
    monkeypatch.setenv("ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", "0.0")
    assert _accept_panel_voice_claim(_claim(0.99, user="admin"), caller) is None
    assert _accept_panel_voice_claim(_claim(0.99, user="admin"), DEVICE_CALLER) == "admin"    # negative control: same payload, device token


def test_claim_threshold_is_read_per_call(monkeypatch):
    payload = _claim(0.85)
    monkeypatch.setenv("ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", "0.9")
    assert _accept_panel_voice_claim(payload, DEVICE_CALLER) is None
    monkeypatch.setenv("ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", "0.8")
    assert _accept_panel_voice_claim(payload, DEVICE_CALLER) == "jason"


@pytest.mark.parametrize("payload", [
    None,
    {},
    {"voice_user_id": "", "voice_score": 0.99, "voice_model_id": R34},
    {"voice_user_id": "jason", "voice_model_id": R34},                        # no score at all
    {"voice_user_id": "jason", "voice_score": None, "voice_model_id": R34},
    {"voice_user_id": "jason", "voice_score": "high", "voice_model_id": R34},  # unparseable score
])
def test_claim_ignored_without_valid_score(payload, monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", "0.0")
    # Even a zero threshold must not accept a claim that carries no real score.
    assert _accept_panel_voice_claim(payload, DEVICE_CALLER) is None


def test_unparseable_threshold_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", "not-a-float")      # an override that cannot parse never loosens the bar
    assert _accept_panel_voice_claim(_claim(0.27), DEVICE_CALLER) == "jason"
    assert _accept_panel_voice_claim(_claim(0.26), DEVICE_CALLER) is None


# ── 1b. consent re-check on the claim path ─────────────────────────────────
# A panel whose profile cache predates a consent revocation keeps sending
# passing-score claims; the server re-checks consent_at in the DB and fails
# CLOSED on any DB error (dropping the claim == the no-claim outcome).

class _ConsentDB:
    def __init__(self, has_row):
        self.has_row = has_row
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append((sql, tuple(params)))
        has_row = self.has_row

        class _Cur:
            async def fetchone(self):
                return (1,) if has_row else None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return _Cur()


def _install_consent_db(monkeypatch, has_row):
    db = _ConsentDB(has_row)

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield db

    mod = types.ModuleType("db_compat")
    mod.get_compat_db = fake_ctx
    monkeypatch.setitem(sys.modules, "db_compat", mod)
    return db


@pytest.mark.asyncio
async def test_claim_consent_check_true_when_consented(monkeypatch):
    db = _install_consent_db(monkeypatch, has_row=True)
    assert await voice_tts._voice_claim_consented("jason") is True
    assert "consent_at IS NOT NULL" in db.queries[0][0]
    assert db.queries[0][1] == ("jason",)


@pytest.mark.asyncio
async def test_claim_consent_check_false_after_revocation(monkeypatch):
    _install_consent_db(monkeypatch, has_row=False)
    assert await voice_tts._voice_claim_consented("jason") is False


@pytest.mark.asyncio
async def test_claim_consent_check_fails_closed_on_db_error(monkeypatch):
    @contextlib.asynccontextmanager
    async def broken_ctx():
        raise RuntimeError("pool exhausted")
        yield

    mod = types.ModuleType("db_compat")
    mod.get_compat_db = broken_ctx
    monkeypatch.setitem(sys.modules, "db_compat", mod)
    assert await voice_tts._voice_claim_consented("jason") is False


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
        ("jason", "Jason", EMB_JASON, 3, R34),
        ("zoe-kid", "Kiddo", EMB_ZOE, None, R34),
        ("old-voice", "Old", EMB_ZOE, 3, "resemblyzer"),       # a retired-model row is never served
    ])

    out = await voice_profiles_sync(models=R34, caller=dict(DEVICE_CALLER))

    assert out["ok"] is True
    assert out["threshold"] == pytest.approx(0.87)
    assert [p["user_id"] for p in out["profiles"]] == ["jason", "zoe-kid"]
    assert base64.b64decode(out["profiles"][0]["embedding_base64"]) == EMB_JASON
    assert out["profiles"][1]["sample_count"] == 1  # NULL count normalises to 1
    # The consent gate must be part of the SQL itself, not post-filtering.
    assert any("consent_at IS NOT NULL" in q for q in db.queries)


# ── 3. enroll consent plumbing ─────────────────────────────────────────────

class _WriteDB(_FakeDB):
    """Execute double for the enroll path.

    ``existing_row`` selects the branch: None → INSERT (new profile);
    an (id,) tuple → the UPDATE/re-enroll path.
    """

    def __init__(self, existing_row=None):
        super().__init__(rows=[])
        self.params: list[tuple] = []
        self.existing_row = existing_row

    def execute(self, sql, params=()):
        self.queries.append(sql)
        self.params.append(tuple(params))
        existing = self.existing_row

        class _Cur(_FakeCursor):
            async def fetchone(self):
                return existing

            # Write sites do `await db.execute(...)`; read sites use
            # `async with db.execute(...)`. Support both shapes.
            def __await__(self):
                async def _done():
                    return self
                return _done().__await__()

        return _Cur([])

    async def commit(self):
        return None


def _wire_enroll(monkeypatch, db):
    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield db

    mod = types.ModuleType("db_compat")
    mod.get_compat_db = fake_ctx
    monkeypatch.setitem(sys.modules, "db_compat", mod)


EMB_R34 = (np.arange(256, dtype=np.float32) / np.linalg.norm(np.arange(256, dtype=np.float32))).tobytes()


def _enroll_payload(consent):
    p = {
        "embedding_base64": base64.b64encode(EMB_R34).decode(),      # a finished panel-built profile
        "model_id": R34,
        "sample_count": 5,
        "user_id": "jason",
        "display_name": "Jason",
    }
    if consent is not None:
        p["consent"] = consent
    return p


@pytest.mark.asyncio
@pytest.mark.parametrize("consent,expect_stamp", [(True, True), (False, False), (None, False)])
async def test_enroll_insert_stamps_consent_only_when_given(monkeypatch, consent, expect_stamp):
    db = _WriteDB(existing_row=None)
    _wire_enroll(monkeypatch, db)

    out = await voice_tts.voice_enroll(
        _enroll_payload(consent), caller=dict(DEVICE_CALLER))

    assert out["ok"] is True
    inserts = [q for q in db.queries if "INSERT INTO speaker_profiles" in q]
    assert len(inserts) == 1
    assert ("consent_at" in inserts[0]) is expect_stamp


EXISTING = ("pid-old",)  # (id,)


@pytest.mark.asyncio
@pytest.mark.parametrize("consent,expect", [
    (True, "stamp"),    # re-consent stamps CURRENT_TIMESTAMP
    (False, "revoke"),  # explicit revocation clears consent_at → out of match pool
    (None, "untouched"),  # absent field leaves existing consent as-is
])
async def test_reenroll_consent_stamp_revoke_untouched(monkeypatch, consent, expect):
    db = _WriteDB(existing_row=EXISTING)
    _wire_enroll(monkeypatch, db)

    out = await voice_tts.voice_enroll(
        _enroll_payload(consent), caller=dict(DEVICE_CALLER))

    assert out["ok"] is True and out["profile_id"] == "pid-old"
    consent_updates = [q for q in db.queries if "SET consent_at" in q]
    if expect == "stamp":
        assert len(consent_updates) == 1 and "CURRENT_TIMESTAMP" in consent_updates[0]
    elif expect == "revoke":
        assert len(consent_updates) == 1 and "consent_at=NULL" in consent_updates[0]
    else:
        assert consent_updates == []


@pytest.fixture(autouse=True)
def _member_signed_in_at_the_panel(jason_signed_in_at_the_panel):
    """A device-token enrolment writes ONLY a member the server verified at the panel (biometric_scope, 2026-10-19); these tests
    are about the payload, so "jason" is PIN-signed-in. The identity rule itself: test_biometric_ownership_scope.py."""
