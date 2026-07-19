"""Push self-prune must also catch PERMANENTLY-malformed subscriptions.

The 410/404 prune only matched failures carrying an HTTP status from the push
service. A subscription with malformed crypto keys ("Invalid p256dh key
specified") fails CLIENT-SIDE, before any status exists — so it was retried on
every send forever. A test.example.com junk subscription did exactly this,
failing every morning brief since June (seen live on the first spoken brief,
2026-07-19).

Fakes only: pywebpush is replaced with a stub module, the DB with a recorder.
"""
from __future__ import annotations

import sys
import types

import pytest

pytestmark = pytest.mark.ci_safe

import routers.push as push


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows
        self.deletes: list[tuple] = []
        self.committed = False

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("DELETE"):
            self.deletes.append(tuple(params))

            class _Done:
                def __await__(self):
                    async def _n():
                        return None
                    return _n().__await__()

            return _Done()
        return _FakeCursor(self._rows)

    async def commit(self):
        self.committed = True


def _wire(monkeypatch, *, rows, exc_message):
    db = _FakeDB(rows)

    async def fake_get_db():
        yield db

    monkeypatch.setattr(push, "get_db", fake_get_db)
    monkeypatch.setattr(push, "_get_vapid_keys", lambda: {"private_key": "k", "claims": {}})

    class FakeWebPushException(Exception):
        pass

    def fake_webpush(**_kwargs):
        raise FakeWebPushException(exc_message)

    fake_mod = types.ModuleType("pywebpush")
    fake_mod.webpush = fake_webpush
    fake_mod.WebPushException = FakeWebPushException
    monkeypatch.setitem(sys.modules, "pywebpush", fake_mod)
    return db


_ROW = {
    "endpoint": "https://test.example.com/push-abc",
    "keys_p256dh": "malformed",
    "keys_auth": "malformed",
}


@pytest.mark.asyncio
async def test_invalid_p256dh_subscription_is_pruned(monkeypatch):
    db = _wire(monkeypatch, rows=[dict(_ROW)], exc_message="Invalid p256dh key specified")

    sent = await push.send_push_to_user("jason", message="hi")

    assert sent == 0
    assert db.deletes == [("jason", _ROW["endpoint"])], (
        "a malformed-key subscription must be pruned, not retried forever"
    )
    assert db.committed


@pytest.mark.asyncio
async def test_gone_410_subscription_still_pruned(monkeypatch):
    db = _wire(monkeypatch, rows=[dict(_ROW)], exc_message="Push failed: 410 Gone")

    await push.send_push_to_user("jason", message="hi")

    assert db.deletes == [("jason", _ROW["endpoint"])]


@pytest.mark.asyncio
async def test_transient_failure_is_NOT_pruned(monkeypatch):
    """The prune must stay narrow: a network blip carries neither an HTTP-gone
    status nor a key-format marker, and the subscription must survive it."""
    db = _wire(monkeypatch, rows=[dict(_ROW)], exc_message="Connection timed out")

    await push.send_push_to_user("jason", message="hi")

    assert db.deletes == [], "a transient failure must never prune a subscription"


@pytest.mark.asyncio
async def test_http_auth_rejection_is_NOT_pruned(monkeypatch):
    """Greptile (PR #1419): "invalid auth" substring-matches "invalid
    authorization" — a phrase a proxy/CDN can return in a transient 401 body.
    If pywebpush surfaces the response body in the exception, a VAPID hiccup
    must NOT permanently prune a healthy subscription. The marker is therefore
    the exact client-side error, "invalid auth key"."""
    db = _wire(
        monkeypatch,
        rows=[dict(_ROW)],
        exc_message="401 Unauthorized: invalid authorization header",
    )

    await push.send_push_to_user("jason", message="hi")

    assert db.deletes == [], "a transient VAPID auth rejection must never prune"


@pytest.mark.asyncio
async def test_invalid_auth_key_subscription_is_pruned(monkeypatch):
    db = _wire(monkeypatch, rows=[dict(_ROW)], exc_message="Invalid auth key specified")

    await push.send_push_to_user("jason", message="hi")

    assert db.deletes == [("jason", _ROW["endpoint"])]
