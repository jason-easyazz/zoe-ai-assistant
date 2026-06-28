"""Regression tests for P2 SQLite-datetime portability in proactive triggers.

The slow-loop triggers used SQLite `datetime('now', '-N days')`, which is not
native Postgres. The slow loop swallows trigger errors, so a rejected query made
these nudges silently never fire. They now use Postgres interval syntax.

Note: the fix uses `(CURRENT_TIMESTAMP - INTERVAL 'N days')::text`, NOT
`NOW() - INTERVAL 'N days'`, because the asyncpg compat shim rewrites bare
`NOW()` → `NOW()::text` (so `NOW() - INTERVAL ...` would become invalid
`text - interval` SQL). The `::text` cast is required because `chat_sessions.
created_at` is a TEXT column. See test_bare_now_interval_would_be_mangled.
"""
import inspect

import pytest

from db_pool import _adapt_params

import proactive.triggers.evening_windown as ew
import proactive.triggers.morning_checkin as mc
import proactive.triggers.evolution_weekly_digest as ed


@pytest.mark.parametrize(
    "trigger_cls",
    [ew.EveningWindDownTrigger, mc.MorningCheckInTrigger, ed.EvolutionWeeklyDigestTrigger],
)
def test_triggers_use_pg_interval_not_sqlite_datetime(trigger_cls):
    src = inspect.getsource(trigger_cls)
    assert "datetime('now'" not in src, "SQLite datetime() must not survive"
    assert "INTERVAL" in src, "must use Postgres INTERVAL syntax"


def test_pg_interval_survives_compat_shim_and_preserves_behaviour():
    expr = (
        "SELECT 1 FROM chat_sessions "
        "WHERE created_at > (CURRENT_TIMESTAMP - INTERVAL '7 days')::text"
    )
    adapted, _ = _adapt_params(expr, ())
    # Stable: no $-placeholder mangling, no NOW()::text rewrite.
    assert adapted == expr
    # The legacy SQLite form the shim used to rewrite produces IDENTICAL adapted
    # SQL, so the chosen expression preserves behaviour exactly.
    legacy = "SELECT 1 FROM chat_sessions WHERE created_at > datetime('now', '-7 days')"
    assert _adapt_params(legacy, ())[0] == adapted


def test_bare_now_interval_would_be_mangled():
    # Documents WHY CURRENT_TIMESTAMP is used instead of NOW(): the shim turns
    # bare NOW() into NOW()::text, yielding invalid `text - interval` SQL.
    bad = "SELECT 1 WHERE created_at > NOW() - INTERVAL '7 days'"
    assert "NOW()::text - INTERVAL" in _adapt_params(bad, ())[0]
