"""The signed-in user's name is IDENTITY (from auth), not a memory fact.

'What's my name' must be answerable from who the user authenticated as, even with
zero stored memories — so the voice brain turn injects the users-table name into
the [About you] block. Guests/daemon/admin get nothing.
"""
import pytest
import asyncio

import routers.voice_tts as v

pytestmark = pytest.mark.ci_safe


def _run(c):
    return asyncio.run(c)


class _FakeDB:
    """Mimics the asyncpg-compat db: fetchrow returns a dict-like Record."""
    def __init__(self, name):
        self._name = name

    async def fetchrow(self, *a, **k):
        return {"name": self._name} if self._name is not None else None


class _FakeCtx:
    def __init__(self, name):
        self._name = name

    async def __aenter__(self):
        return _FakeDB(self._name)

    async def __aexit__(self, *a):
        return False


def test_identity_titlecases_lowercase_name(monkeypatch):
    import db_pool
    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: _FakeCtx("jason"))
    assert _run(v._voice_user_identity("jason")) == "Jason"


def test_identity_preserves_mixed_case(monkeypatch):
    import db_pool
    monkeypatch.setattr(db_pool, "get_db_ctx", lambda: _FakeCtx("McDonald"))
    assert _run(v._voice_user_identity("u1")) == "McDonald"


def test_identity_skips_guest_and_admin():
    assert _run(v._voice_user_identity("guest")) is None
    assert _run(v._voice_user_identity("family-admin")) is None
    assert _run(v._voice_user_identity("")) is None


def test_brain_memory_injects_identity_into_portrait(monkeypatch):
    async def _ident(_uid):
        return "Jason"

    async def _packet(_t, _u):
        return None

    monkeypatch.setattr(v, "_voice_user_identity", _ident)
    monkeypatch.setattr(v, "_voice_recall_packet", _packet)
    import user_portrait

    async def _portrait(_u):
        return "Likes tea."

    monkeypatch.setattr(user_portrait, "load_portrait", _portrait)
    _db, portrait = _run(v._voice_brain_memory("jason", "what is my name"))
    assert "speaking with Jason" in portrait
    assert "Likes tea." in portrait  # original portrait preserved
