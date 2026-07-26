"""Face-ID storage router contracts (`routers/face_id.py`).

The server is storage + policy only — no vision models. Guards:

1. Everything is gated by ``ZOE_FACE_ID_ENABLED`` (default off → 503), read
   per call so a .env flip applies without a code change.
2. Enroll REQUIRES explicit consent (400 without — no unconsented face state,
   unlike speaker profiles where consent is a later stamp), a real user_id,
   a known model/dim, and an embedding whose byte length matches ``dim``.
3. The per-user gallery is capped: enrolling past the limit drops the oldest
   rows instead of erroring.
4. ``GET /api/face/profiles/sync`` hands out biometric embeddings → device
   tokens only (browser session gets 403); the metadata list never includes
   embeddings.

No models, no network, no live DB — the compat-DB context manager is faked,
so this runs in the slim GitHub ``ci_safe`` lane.
"""

from __future__ import annotations

import base64
import contextlib
import sys
import types

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import routers.face_id as face_id


DEVICE = {"source": "device", "panel_id": "zoe-touch-pi", "user_id": "voice-daemon"}
SESSION = {"source": "session", "user_id": "jason", "role": "admin"}
EMB_512 = b"\x01\x02\x03\x04" * 512  # 512 floats * 4 bytes


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _payload(**over):
    p = {
        "embedding_base64": _b64(EMB_512),
        "user_id": "jason",
        "display_name": "Jason",
        "model_name": "buffalo_sc/w600k_mbf",
        "dim": 512,
        "consent": True,
    }
    p.update(over)
    return p


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __await__(self):
        async def _done():
            return self
        return _done().__await__()


class _FakeDB:
    """Compat-DB double: records queries+params, serves canned SELECT rows."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries: list[str] = []
        self.params: list[tuple] = []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        self.params.append(tuple(params))
        return _FakeCursor(self.rows)

    async def commit(self):
        return None


def _install_fake_db(monkeypatch, rows=()):
    db = _FakeDB(rows)

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield db

    mod = types.ModuleType("db_compat")
    mod.get_compat_db = fake_ctx
    monkeypatch.setitem(sys.modules, "db_compat", mod)
    return db


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ZOE_FACE_ID_ENABLED", "true")


# ── 1. flag gate ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("call", [
    lambda: face_id.face_enroll(_payload(), caller=dict(DEVICE)),
    lambda: face_id.face_profiles_sync(caller=dict(DEVICE)),
    lambda: face_id.face_profiles(caller=dict(DEVICE)),
    lambda: face_id.face_profile_delete("pid", caller=dict(DEVICE)),
])
async def test_everything_503s_when_disabled(monkeypatch, call):
    monkeypatch.delenv("ZOE_FACE_ID_ENABLED", raising=False)
    with pytest.raises(HTTPException) as exc:
        await call()
    assert exc.value.status_code == 503


# ── 2. enroll validation ───────────────────────────────────────────────────

@pytest.mark.parametrize("bad,status_detail", [
    (_payload(consent=False), "consent"),
    (_payload(user_id="voice-daemon"), "user_id"),
    (_payload(user_id="guest"), "user_id"),
    (_payload(model_name=""), "model_name"),
    (_payload(dim="huge"), "dim"),
    (_payload(dim=100), "dim"),
    (_payload(embedding_base64=""), "embedding_base64"),
    (_payload(embedding_base64="!!not-base64!!"), "base64"),
    (_payload(embedding_base64=_b64(b"\x00" * 12)), "length"),  # 12 bytes != 512*4
])
async def test_enroll_rejects_bad_payloads(monkeypatch, enabled, bad, status_detail):
    _install_fake_db(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await face_id.face_enroll(bad, caller=dict(DEVICE))
    assert exc.value.status_code == 400
    assert status_detail in str(exc.value.detail)


async def test_enroll_stores_with_consent_timestamp(monkeypatch, enabled):
    db = _install_fake_db(monkeypatch, rows=[])
    out = await face_id.face_enroll(_payload(), caller=dict(DEVICE))
    assert out["ok"] is True and out["user_id"] == "jason"
    insert = next(q for q in db.queries if "INSERT INTO face_profiles" in q)
    assert "consent_at" in insert and "CURRENT_TIMESTAMP" in insert


async def test_enroll_caps_per_user_gallery(monkeypatch, enabled):
    # 10 existing rows: the two oldest (beyond the 8 cap) must be deleted.
    rows = [(f"pid-{i}",) for i in range(10)]
    db = _install_fake_db(monkeypatch, rows=rows)
    await face_id.face_enroll(_payload(), caller=dict(DEVICE))
    deletes = [p for q, p in zip(db.queries, db.params) if "DELETE FROM face_profiles" in q]
    assert deletes == [("pid-8",), ("pid-9",)]


# ── 3. sync auth + shape ───────────────────────────────────────────────────

async def test_sync_rejects_browser_sessions(monkeypatch, enabled):
    with pytest.raises(HTTPException) as exc:
        await face_id.face_profiles_sync(caller=dict(SESSION))
    assert exc.value.status_code == 403


async def test_sync_returns_embeddings_and_threshold(monkeypatch, enabled):
    monkeypatch.setenv("ZOE_FACE_ID_THRESHOLD", "0.5")
    _install_fake_db(monkeypatch, rows=[
        ("jason", "Jason", EMB_512, "buffalo_sc/w600k_mbf", 512),
    ])
    out = await face_id.face_profiles_sync(caller=dict(DEVICE))
    assert out["threshold"] == pytest.approx(0.5)
    assert base64.b64decode(out["profiles"][0]["embedding_base64"]) == EMB_512
    assert out["profiles"][0]["dim"] == 512


async def test_profiles_list_never_exposes_embeddings(monkeypatch, enabled):
    _install_fake_db(monkeypatch, rows=[
        ("pid-1", "jason", "Jason", "buffalo_sc/w600k_mbf", 512, "zoe-touch-pi", "2026-07-19"),
    ])
    out = await face_id.face_profiles(caller=dict(SESSION))
    assert out["profiles"][0]["id"] == "pid-1"
    assert "embedding_base64" not in out["profiles"][0]
    assert "embedding_blob" not in out["profiles"][0]


async def test_threshold_default_survives_bad_env(monkeypatch):
    monkeypatch.setenv("ZOE_FACE_ID_THRESHOLD", "nope")
    assert face_id._face_id_threshold() == pytest.approx(0.45)
