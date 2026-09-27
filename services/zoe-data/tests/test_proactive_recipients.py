"""proactive/recipients.py — who the morning brief / evolution digest / evening
prompt are for. The fake DB is SEMANTIC (each predicate applies only when present
in the SQL sent), so dropping the ``panel_user_bindings`` arm reddens
``test_bound_member_gets_brief_without_recent_activity`` and dropping the
synthetic filter reddens ``test_synthetic_and_guest_never_recipients``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import proactive.recipients as rcp
import proactive.triggers.evening_windown as ew
import proactive.triggers.morning_checkin as mc

GUESTS = ("guest", "anonymous", "voice-guest", "voice-daemon", "")


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r
        return gen()

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class _Exec:
    def __init__(self, factory):
        self._factory = factory

    def __await__(self):
        return self._factory().__await__()

    async def __aenter__(self):
        return await self._factory()

    async def __aexit__(self, *_):
        return False


class HouseholdDB:
    """bindings (panel_id, user_id, type); messages dicts; names {user_id: name}."""

    def __init__(self, bindings=(), messages=(), names=None, fail=()):
        self.bindings = list(bindings)
        self.messages = list(messages)
        self.names = names or {}
        self.fail = set(fail)
        self.queries: list[str] = []

    def execute(self, sql, params=()):
        return _Exec(lambda: self._do(sql, params))

    async def _do(self, sql, params):
        norm = " ".join(sql.split()).lower()
        self.queries.append(norm)
        if "from panel_user_bindings" in norm:
            if "bindings" in self.fail:
                raise RuntimeError("bindings table unavailable")
            rows = [b for b in self.bindings
                    if "binding_type = 'default'" not in norm or b[2] == "default"]
            return _Cursor([(b[1], self.names.get(b[1])) for b in rows])
        if "from chat_messages cm" in norm:
            if "messages" in self.fail:
                raise RuntimeError("chat tables unavailable")
            msgs = list(self.messages)
            if "cm.role = 'user'" in norm:
                msgs = [m for m in msgs if m["role"] == "user"]
            if "cm.created_at::timestamptz" in norm and "interval '7 days'" in norm:
                msgs = [m for m in msgs if m["age_days"] < 7]
            owners = []
            for m in msgs:
                owner = (m["meta_owner"] if (m["meta_owner"] or "") not in GUESTS
                         else m["session_owner"] if (m["session_owner"] or "") not in GUESTS
                         else None)
                if owner and owner not in owners:
                    owners.append(owner)
            return _Cursor([(o, self.names.get(o)) for o in owners])
        # proactive_pending, morning-context enrichment, ... → nothing
        return _Cursor([])


def _msg(owner, *, age_days=1, session_owner=None, role="user"):
    return {"meta_owner": owner, "session_owner": session_owner or owner,
            "role": role, "age_days": age_days}


KIOSK = [("zoe-touch-pi", "member-a", "default")]


async def test_bound_member_gets_brief_without_recent_activity():
    # The 09-11 regression: a bound member with no turn in 7 days still gets it.
    db = HouseholdDB(bindings=KIOSK, messages=[_msg("member-a", age_days=24)],
                     names={"member-a": "Alex"})
    assert await rcp.proactive_recipients(db, pass_name="t") == [("member-a", "Alex")]


async def test_active_user_counted_by_message_not_session_open_time():
    db = HouseholdDB(messages=[_msg("member-b", age_days=1)])
    assert await rcp.proactive_recipients(db, pass_name="t") == [("member-b", "")]
    assert any("cm.created_at::timestamptz" in q for q in db.queries)
    assert not any("cs.created_at" in q for q in db.queries)


async def test_guest_session_turn_is_owned_by_the_metadata_speaker():
    db = HouseholdDB(messages=[_msg("member-b", session_owner="guest")])
    assert await rcp.proactive_recipients(db, pass_name="t") == [("member-b", "")]


async def test_stale_unbound_user_and_non_default_binding_are_not_recipients():
    db = HouseholdDB(bindings=[("panel-hall", "member-c", "allowed")],
                     messages=[_msg("member-d", age_days=30),
                               _msg("member-e", role="assistant")])
    assert await rcp.proactive_recipients(db, pass_name="t") == []


async def test_synthetic_and_guest_never_recipients(caplog):
    db = HouseholdDB(
        bindings=KIOSK + [("panel-lab", "demo_lab", "default")],
        messages=[_msg("test-route-probe"), _msg("test-sec-b-4f9c0c"),
                  _msg("guest", session_owner="guest"), _msg("member-a")],
    )
    with caplog.at_level(logging.INFO, logger=rcp.log.name):
        got = await rcp.proactive_recipients(db, pass_name="morning_checkin")
    assert got == [("member-a", "")]
    assert "morning_checkin: users kept=1 skipped_synthetic=3" in caplog.text
    assert "test-route-probe" not in caplog.text


async def test_evening_scope_excludes_quiet_bound_member():
    db = HouseholdDB(bindings=KIOSK, messages=[_msg("member-b")])
    got = await rcp.proactive_recipients(db, pass_name="t", include_panel_members=False)
    assert got == [("member-b", "")]
    assert not any("panel_user_bindings" in q for q in db.queries)


@pytest.mark.parametrize("broken, expected", [
    ("bindings", [("member-b", "")]),
    ("messages", [("member-a", "")]),
])
async def test_one_failing_arm_keeps_the_other(broken, expected):
    db = HouseholdDB(bindings=KIOSK, messages=[_msg("member-b")], fail={broken})
    assert await rcp.proactive_recipients(db, pass_name="t") == expected


# --------------------------------------------------------------------------- #
# The triggers, end to end through check()
# --------------------------------------------------------------------------- #
class _FixedNow(datetime):
    fixed: datetime

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


def _pin_clock(monkeypatch, module, hour, minute):
    _FixedNow.fixed = datetime(2026, 9, 28, hour, minute, tzinfo=module._ZOE_TZ)
    monkeypatch.setattr(module, "datetime", _FixedNow)


async def test_morning_checkin_briefs_the_household_not_the_probe(monkeypatch):
    _pin_clock(monkeypatch, mc, 7, 30)
    db = HouseholdDB(bindings=KIOSK,
                     messages=[_msg("member-a", age_days=24), _msg("test-route-probe")],
                     names={"member-a": "Alex"})
    results = await mc.MorningCheckInTrigger().check(db)
    assert [r.user_id for r in results] == ["member-a"]
    assert results[0].message.startswith("Good morning Alex")
    assert results[0].context["spoken_guest_safe"] == (
        "Good morning Alex — your brief is ready when you are.")


async def test_evening_windown_skips_synthetic(monkeypatch):
    _pin_clock(monkeypatch, ew, 21, 5)
    db = HouseholdDB(bindings=KIOSK,
                     messages=[_msg("member-b"), _msg("test-route-probe")])
    results = await ew.EveningWindDownTrigger().check(db)
    assert [r.user_id for r in results] == ["member-b"]
