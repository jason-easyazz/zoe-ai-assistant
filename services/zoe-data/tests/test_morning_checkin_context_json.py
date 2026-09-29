"""The 07:30 morning check-in must survive DB-typed values in its context.

Live 2026-09-30 07:30: ``fire_notification failed … (type=morning_checkin):
Object of type datetime is not JSON serializable``. ``_build_morning_context``
put ``open_loops.follow_up_after`` (TIMESTAMP -> ``datetime``) into
``ctx["open_loops"][*]["due"]`` (#1781) and ``create_pending`` stored the
context with a bare ``json.dumps``. Latent until open loops existed (#1782).

Pinned on both sides: the gatherer emits a UTC ISO string that
``brief_first_turn._as_utc`` still reads, and the ``proactive_pending`` write
serialises any datetime/Decimal/UUID instead of failing the notification.
Negative controls: restore ``"due": row[3]`` -> the gatherer test goes red;
restore the bare ``json.dumps`` in ``create_pending`` -> the boundary test goes red.
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
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Exec:
    def __init__(self, rows):
        self._cur = _Cur(rows)

    def __await__(self):
        async def _r():
            return self._cur
        return _r().__await__()

    async def __aenter__(self):
        return self._cur

    async def __aexit__(self, *_):
        return False


class _DB:
    """Answers the morning-context reads; records every write."""

    def __init__(self):
        self.writes = []

    def execute(self, sql, params=()):
        if "FROM open_loops" in sql:
            return _Exec([("renew the rego", "", 2, DUE_NAIVE_UTC)])
        if "FROM events" in sql:
            return _Exec([CAL_ROW])
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE")):
            self.writes.append((sql, params))
        return _Exec([])

    async def commit(self):
        pass


@pytest.fixture
def db(monkeypatch):
    fake = _DB()

    @contextlib.asynccontextmanager
    async def fake_compat_db():
        yield fake

    # Never touch a real store: the pending write and the memory read are faked.
    monkeypatch.setattr(session_utils, "_get_compat_db", fake_compat_db)
    monkeypatch.setattr(engine, "_get_compat_db", fake_compat_db)
    mem = types.ModuleType("memory_service")

    def _no_palace():
        raise RuntimeError("no palace in tests")

    mem.get_memory_service = _no_palace
    monkeypatch.setitem(sys.modules, "memory_service", mem)

    async def fake_compose(trigger_type, ctx, fallback=""):
        return fallback

    async def no_speak(**_kw):
        return None

    async def fake_push(user_id, message, extra=None):
        return 1

    monkeypatch.setattr(engine, "compose_message", fake_compose)
    monkeypatch.setattr(engine, "_maybe_speak_notification", no_speak)
    monkeypatch.setattr(engine, "_send_push", fake_push)
    return fake


def _stored_context(db) -> dict:
    [(_sql, params)] = [w for w in db.writes if "proactive_pending" in w[0]]
    return json.loads(params[5])


async def test_gatherer_emits_a_json_safe_utc_iso_due(db):
    ctx = await _build_morning_context(db, "member-a", "2026-09-30", include_board=False)
    assert ctx["open_loops"][0]["due"] == "2026-09-30T18:33:27+00:00"
    assert ctx["calendar"][0]["start"] == "09:30"
    json.dumps(ctx)  # plain dumps: the gathered context itself is JSON-native


async def test_morning_checkin_fire_notification_stores_the_context(db):
    ctx = await _build_morning_context(db, "member-a", "2026-09-30", include_board=False)
    await engine.fire_notification(user_id="member-a", message="Good morning",
                                   trigger_type="morning_checkin", item_id="morning_checkin",
                                   context={**ctx, "force_send": True})
    stored = _stored_context(db)
    assert stored["open_loops"][0]["due"] == "2026-09-30T18:33:27+00:00"
    assert stored["calendar"][0]["title"] == "Dentist"


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
