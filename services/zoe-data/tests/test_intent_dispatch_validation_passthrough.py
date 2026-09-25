"""A validation verdict from the fulfilment path must reach the brain as 4xx, not 500.

Codex P2 on #1686: `reminder_service.normalize_due_date` raises 422 for an
unfireable due_date, and the direct reminders API returns exactly that — but the
brain's `add_reminder` tool goes through `routers/system.py::intent_dispatch`,
whose catch-all turned every exception into `500 intent execution failed`. The
tool then reported a generic service failure instead of "that date is not valid".

Driven through the REAL chain (intent_dispatch → execute_intent →
_execute_reminder_create_direct → create_reminder_record → normalize_due_date)
with only the DB and the identity lookup faked.

Negative control: with the catch-all restored (HTTPException swallowed), the
same call yields 500 — asserted by monkeypatching the boundary back.
"""
from __future__ import annotations

import contextlib

import pytest
from fastapi import HTTPException

import intent_router
import routers.system as system

pytestmark = pytest.mark.ci_safe


class _Cursor:
    async def fetchone(self):
        return None


class _FakeDb:
    def __init__(self):
        self.inserted = []

    async def execute(self, sql, params=()):
        if "INSERT INTO reminders" in sql:
            self.inserted.append(tuple(params))
        return _Cursor()

    async def commit(self):
        return None


@pytest.fixture
def _dispatch_env(monkeypatch):
    db = _FakeDb()

    @contextlib.asynccontextmanager
    async def _ctx():
        yield db

    import database

    monkeypatch.setattr(database, "get_db_ctx", _ctx)

    async def _user(_db, user_id):
        return {"user_id": user_id, "role": "admin"}

    async def _allow(*_a, **_k):
        return None

    async def _quiet(*_a, **_k):
        return None

    monkeypatch.setattr(intent_router, "_load_direct_execution_user", _user)
    monkeypatch.setattr("reminder_service.require_feature_access", _allow)
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", _quiet)
    return db


def _body(date: str):
    return system._IntentDispatchBody(
        user_id="jason", intent="reminder_create", slots={"title": "handover for the van", "date": date}
    )


@pytest.mark.asyncio
async def test_invalid_date_via_dispatch_is_422_not_500(_dispatch_env):
    with pytest.raises(HTTPException) as exc:
        await system.intent_dispatch(_body("June 31"), None)

    assert exc.value.status_code == 422
    assert "due_date" in exc.value.detail
    assert _dispatch_env.inserted == [], "nothing may be stored for an unfireable date"


@pytest.mark.asyncio
async def test_negative_control_catch_all_would_have_made_it_500(_dispatch_env, monkeypatch):
    """Guard the guard: swallow HTTPException at the boundary again → 500."""
    real_execute = intent_router.execute_intent

    async def _swallowing(intent, user_id="guest"):
        try:
            return await real_execute(intent, user_id=user_id)
        except HTTPException as e:  # the old catch-all's effect
            raise RuntimeError("swallowed") from e

    monkeypatch.setattr(intent_router, "execute_intent", _swallowing)
    with pytest.raises(HTTPException) as exc:
        await system.intent_dispatch(_body("June 31"), None)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_unexpected_failure_is_still_a_500(_dispatch_env, monkeypatch):
    async def _boom(intent, user_id="guest"):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(intent_router, "execute_intent", _boom)
    with pytest.raises(HTTPException) as exc:
        await system.intent_dispatch(_body("2026-06-30"), None)
    assert exc.value.status_code == 500
