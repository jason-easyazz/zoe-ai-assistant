"""The 07:30 morning check-in must survive DB-typed values in its context.

Live 2026-09-30: ``fire_notification failed … (type=morning_checkin): Object of
type datetime is not JSON serializable`` — ``open_loops.follow_up_after`` rode
into the context as ``due`` (#1781) and ``create_pending`` used a bare
``json.dumps``; latent until open loops existed (#1782). Negative controls:
``"due": row[3]`` reddens the gatherer test; a bare ``json.dumps`` in
``create_pending`` reddens the boundary test.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import contextlib
import json
import sys
import types
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import brief_first_turn as bft
import proactive.engine as engine
import proactive.session_utils as session_utils
from proactive.triggers.morning_checkin import _build_morning_context

DUE_NAIVE_UTC = datetime(2026, 9, 30, 18, 33, 27)  # what asyncpg returns for TIMESTAMP
CAL_ROW = ("Dentist", "09:30", "10:00", "Main St", "", 30)


class _Cur:
    """db_compat's execute() shape: awaitable AND an async context manager."""

    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _DB:
    """Answers the morning-context reads; records every write."""

    def __init__(self):
        self.writes = []

    def execute(self, sql, params=()):
        if "FROM open_loops" in sql:
            return _Cur([("renew the rego", "", 2, DUE_NAIVE_UTC, 7)])  # SELECT order, + id
        if "FROM events" in sql:
            return _Cur([CAL_ROW])
        self.writes.append((sql, params))
        return _Cur([])

    async def commit(self):
        pass


@pytest.fixture
def db(monkeypatch):
    fake = _DB()

    @contextlib.asynccontextmanager
    async def fake_compat_db():
        yield fake

    async def passthrough(trigger_type, ctx, fallback=""):
        return fallback

    async def nothing(**_kw):
        return None

    async def one_subscriber(user_id, message, extra=None):
        return 1

    # Never touch a real store: the pending write and the memory read are faked.
    monkeypatch.setattr(session_utils, "_get_compat_db", fake_compat_db)
    monkeypatch.setattr(engine, "_get_compat_db", fake_compat_db)
    monkeypatch.setitem(sys.modules, "memory_service", types.ModuleType("memory_service"))
    monkeypatch.setattr(engine, "compose_message", passthrough)
    monkeypatch.setattr(engine, "_maybe_speak_notification", nothing)
    monkeypatch.setattr(engine, "_send_push", one_subscriber)
    return fake


def _stored_context(db) -> dict:
    [(_sql, params)] = [w for w in db.writes if "proactive_pending" in w[0]]
    return json.loads(params[5])


async def test_morning_context_due_is_utc_iso_and_is_stored(db):
    ctx = await _build_morning_context(db, "member-a", "2026-09-30", include_board=False)
    assert ctx["open_loops"][0]["due"] == "2026-09-30T18:33:27+00:00"
    json.dumps(ctx)  # plain dumps: the gathered context itself is JSON-native
    await engine.fire_notification(user_id="member-a", message="Good morning",
                                   trigger_type="morning_checkin", item_id="morning_checkin",
                                   context={**ctx, "force_send": True})
    stored = _stored_context(db)
    assert stored["open_loops"][0]["due"] == "2026-09-30T18:33:27+00:00"
    assert stored["calendar"][0]["start"] == "09:30"


async def test_boundary_serialises_db_types_instead_of_failing(db):
    uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    ctx = {"force_send": True,
           "open_loops": [{"text": "x", "due": DUE_NAIVE_UTC}],
           "calendar": [{"start": datetime(2026, 9, 30, 9, 30, tzinfo=timezone.utc),
                         "end_date": date(2026, 9, 30)}],
           "weight": Decimal("2.5"), "person_id": uid}
    await engine.fire_notification(user_id="member-a", message="m",
                                   trigger_type="morning_checkin", context=ctx)
    stored = _stored_context(db)
    assert stored["open_loops"][0]["due"] == "2026-09-30T18:33:27"
    assert stored["calendar"][0] == {"start": "2026-09-30T09:30:00+00:00", "end_date": "2026-09-30"}
    assert stored["weight"] == 2.5 and stored["person_id"] == str(uid)


@pytest.mark.parametrize("due, overdue", [
    ("2026-09-30T18:33:27+00:00", False),   # gatherer form, later today in UTC
    ("2026-09-29T20:00:00+00:00", True),    # gatherer form, passed
    ("2026-09-29T20:00:00", True),          # legacy naive string (read as UTC)
    (datetime(2026, 9, 29, 20, 0), True),   # raw datetime still accepted
])
def test_brief_day_items_reads_the_due_form(due, overdue):
    local_now = datetime(2026, 9, 30, 8, 0, tzinfo=ZoneInfo("Australia/Perth"))  # 00:00 UTC
    ctx = {"open_loops": [{"text": "renew rego", "hint": "", "weight": 1, "due": due}]}
    items, critical = bft.day_items(ctx, local_now)
    assert items == ["To follow up: renew rego"]
    assert critical == (["Overdue: renew rego"] if overdue else [])
