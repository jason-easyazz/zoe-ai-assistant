"""Deterministic coverage for the morning brief — Samantha criterion #3
(surfaces relevant memory UNPROMPTED).

The proactive engine's morning check-in, NOT nondeterministic in-turn model
behaviour, is the mechanism that surfaces memory unprompted: it pulls recent
emotional moments + calendar + portrait and composes a pushed brief. In-turn
4B surfacing was measured ~1/5 and rejected. This locks the deterministic
composition so a regression in the real #3 path is caught (Greptile #1014).
"""

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

from proactive.triggers.morning_checkin import _compose_morning_message


def test_brief_surfaces_recent_emotional_moment():
    # emotional_moments is a list of STRINGS (as the trigger stores them)
    ctx = {"emotional_moments": ["Jason has been anxious about the house settlement"]}
    msg = _compose_morning_message(ctx, "Jason", "Saturday, July 04")
    assert "Good morning Jason" in msg
    assert "settlement" in msg.lower(), msg          # the memory is surfaced unprompted


def test_brief_surfaces_calendar_and_emotion_together():
    ctx = {
        "calendar": [{"title": "Dentist", "start": "3pm"}],
        "emotional_moments": ["Jason was thrilled about the new job"],
    }
    msg = _compose_morning_message(ctx, "Jason", "Monday")
    assert "Dentist" in msg
    assert "thrilled" in msg.lower() or "new job" in msg.lower()


def test_emotion_yields_to_open_loop_to_avoid_double_stacking():
    # The compose deliberately shows the emotional line only when there's no open
    # loop follow-up (avoids double-stacking); lock that branch.
    ctx = {
        "open_loops": [{"hint": "your car service", "text": "car service booking"}],
        "emotional_moments": ["Jason has been stressed about work"],
    }
    msg = _compose_morning_message(ctx, "Jason", "Tuesday")
    assert "car service" in msg
    assert "stressed about work" not in msg.lower()   # emotional line suppressed under a loop


def test_empty_context_is_a_clean_greeting():
    msg = _compose_morning_message({}, "Jason", "Sunday")
    assert msg.startswith("Good morning Jason")
    assert "Ready to start the day" in msg            # generic fallback, no crash


# --------------------------------------------------------------------------- #
# Board summary is ADMIN-ONLY (operator state, not companion content)
# --------------------------------------------------------------------------- #
# The first live spoken brief (2026-07-19, fired as the kiosk guest) opened with
# "there are 5 open items on the board — agents will triage automatically":
# dev-console noise in a companion good-morning, AND household engineering
# state leaked to a non-admin. The board block in _build_morning_context is now
# gated on users.role == 'admin', failing CLOSED on any role-lookup problem.

import sys
import types

from proactive.triggers.morning_checkin import _build_morning_context


class _RoleCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return [self._row] if self._row else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Exec:
    """Awaitable AND async-context, mirroring db_compat's execute() shape
    (production code does `cursor = await db.execute(...)`)."""

    def __init__(self, cursor):
        self._cursor = cursor

    def __await__(self):
        async def _r():
            return self._cursor
        return _r().__await__()

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *_):
        return False


class _RoleOnlyDB:
    """Answers the role query; every other query yields nothing (each context
    block swallows its own failures, so an empty context is fine)."""

    def __init__(self, role):
        self._role = role

    def execute(self, sql, params=()):
        if "role FROM users" in sql:
            return _Exec(_RoleCursor({"role": self._role} if self._role is not None else None))
        return _Exec(_RoleCursor(None))


def _fake_multica(monkeypatch, *, todo=3, in_progress=2):
    calls = {"listed": 0}

    class _MC:
        def is_configured(self):
            return True

        async def list_issues(self, status):
            calls["listed"] += 1
            n = todo if status == "todo" else in_progress
            return [object()] * n

    mod = types.ModuleType("multica_client")
    mod.get_multica_client = lambda: _MC()
    monkeypatch.setitem(sys.modules, "multica_client", mod)
    return calls


@pytest.mark.asyncio
async def test_board_summary_included_for_admin(monkeypatch):
    calls = _fake_multica(monkeypatch)
    ctx = await _build_morning_context(_RoleOnlyDB("admin"), "family-admin", "2026-07-19")
    assert ctx.get("board_summary") == {"pending": 3, "in_progress": 2}
    assert calls["listed"] == 2


@pytest.mark.asyncio
async def test_board_summary_hidden_for_member_and_guest(monkeypatch):
    for role in ("member", "user", "guest"):
        calls = _fake_multica(monkeypatch)
        ctx = await _build_morning_context(_RoleOnlyDB(role), "someone", "2026-07-19")
        assert "board_summary" not in ctx, f"board leaked to role={role!r}"
        assert calls["listed"] == 0, "the board must not even be QUERIED for non-admins"


@pytest.mark.asyncio
async def test_board_summary_fails_closed_on_missing_user(monkeypatch):
    calls = _fake_multica(monkeypatch)
    ctx = await _build_morning_context(_RoleOnlyDB(None), "ghost", "2026-07-19")
    assert "board_summary" not in ctx
    assert calls["listed"] == 0
