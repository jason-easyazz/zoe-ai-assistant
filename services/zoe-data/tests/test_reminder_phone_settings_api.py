"""GET/PUT /api/user/profile/reminder-delivery (routers/user_profile.py): the GET's ``eligible`` and the PUT's allow / 403 come
from ONE rule (``_phone_delivery_eligible``: positively an adult), so they agree for every audience class; a failed or unknown
lookup is not eligible. Invented member id; an in-memory prefs store. Record: docs/knowledge/reminder-phone-delivery.md.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.ci_safe

import enrollment_state
from routers import user_profile

UID = "member-test-1"
API = "/api/user/profile/reminder-delivery"


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class _PrefsDB:
    def __init__(self, prefs):
        self.prefs = dict(prefs)

    async def execute(self, sql, params=()):
        norm = " ".join(sql.split())
        if norm.startswith("SELECT prefs FROM user_preferences WHERE user_id ="):
            return _Cur([{"prefs": json.dumps(self.prefs)}])
        if norm.startswith("INSERT INTO user_preferences"):
            self.prefs.update(json.loads(params[1]))
            return _Cur([])
        return _Cur([])                       # the role matrix read: no override row -> the defaults

    async def commit(self):
        pass


def _client(db):
    app = FastAPI()
    app.include_router(user_profile.router)

    async def get_db():
        yield db

    member = {"user_id": UID, "role": "user"}
    app.dependency_overrides[user_profile.get_current_user] = lambda: member
    app.dependency_overrides[user_profile.require_member] = lambda: member
    app.dependency_overrides[user_profile.get_db] = get_db
    return TestClient(app)


def _audience(monkeypatch, value):
    async def audience_of(user_id, db=None):
        assert user_id == UID
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(enrollment_state, "audience_of", audience_of)


CASES = [("adult", True), ("minor", False), ("guest", False), ("synthetic", False), ("unknown", False),
         ("", False), (RuntimeError("lookup failed"), False)]


@pytest.mark.parametrize("audience,eligible", CASES, ids=[str(c[0]) or "empty" for c in CASES])
def test_get_eligible_and_put_agree_for_every_audience(monkeypatch, audience, eligible):
    _audience(monkeypatch, audience)
    db = _PrefsDB({"telegram_id": "7001"})
    client = _client(db)
    got = client.get(API)
    assert got.status_code == 200, got.text
    assert got.json()["eligible"] is eligible
    put = client.put(API, json={"channels": ["telegram"], "full_text": False})
    assert put.status_code == (200 if eligible else 403), put.text
    assert (put.status_code == 200) is got.json()["eligible"]
    assert ("reminder_delivery" in db.prefs) is eligible           # a refused PUT stores nothing


def test_get_response_is_unchanged_but_for_eligible(monkeypatch):
    _audience(monkeypatch, "adult")
    monkeypatch.setenv("ZOE_REMINDER_PHONE_DELIVERY", "off")
    db = _PrefsDB({"telegram_id": "7001", "reminder_delivery": {"channels": ["telegram"], "full_text": True}})
    assert _client(db).get(API).json() == {"channels": ["telegram"], "full_text": True, "telegram_linked": True,
                                           "mode": "off", "eligible": True}
