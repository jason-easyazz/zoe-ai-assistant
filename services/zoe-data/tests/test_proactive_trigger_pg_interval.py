"""Regression tests for P2 SQLite-datetime portability in proactive triggers.

The slow-loop triggers used SQLite `datetime('now', '-N days')`, which is not
native Postgres. The slow loop swallows trigger errors, so a rejected query made
these nudges silently never fire.

The fix does a REAL temporal comparison by casting the TEXT column:
`created_at::timestamptz > (CURRENT_TIMESTAMP - INTERVAL 'N days')`.

Why not compare as text? `chat_sessions.created_at` is TEXT and is written in
TWO formats: the schema default (`NOW()::text`) renders space-separated
'YYYY-MM-DD HH:MM:SS+00', while app code writes ISO 'YYYY-MM-DDTHH:MM:SSZ'.
A lexical text comparison mis-orders boundary-day rows because ' ' (0x20) sorts
before 'T' (0x54). `text::timestamptz` parses BOTH forms, so the comparison is
format-robust. Why CURRENT_TIMESTAMP not NOW()? The compat shim rewrites bare
NOW() → NOW()::text (see test_bare_now_interval_would_be_mangled).
"""
import inspect
import os
from datetime import datetime, timedelta, timezone

import pytest

from db_pool import _adapt_params

import proactive.triggers.evening_windown as ew
import proactive.triggers.morning_checkin as mc
import proactive.triggers.evolution_weekly_digest as ed


@pytest.mark.parametrize(
    "trigger_cls",
    [ew.EveningWindDownTrigger, mc.MorningCheckInTrigger, ed.EvolutionWeeklyDigestTrigger],
)
def test_triggers_use_temporal_pg_interval(trigger_cls):
    src = inspect.getsource(trigger_cls)
    assert "datetime('now'" not in src, "SQLite datetime() must not survive"
    assert "INTERVAL" in src, "must use Postgres INTERVAL syntax"
    # Must be a real temporal comparison, never the unsound text form.
    if "INTERVAL" in src:
        assert "::timestamptz" in src, "interval comparison must cast created_at to timestamptz"
        assert "INTERVAL '7 days')::text" not in src and "INTERVAL '3 days')::text" not in src, (
            "lexical ::text comparison against TEXT created_at is unsound"
        )


def test_pg_interval_survives_compat_shim():
    expr = (
        "SELECT 1 FROM chat_sessions cs WHERE x = ? AND "
        "cs.created_at::timestamptz > (CURRENT_TIMESTAMP - INTERVAL '7 days')"
    )
    adapted, _ = _adapt_params(expr, ("u",))
    # Placeholder converts; CURRENT_TIMESTAMP/INTERVAL/::timestamptz pass through
    # cleanly with no NOW()::text mangling.
    assert adapted == (
        "SELECT 1 FROM chat_sessions cs WHERE x = $1 AND "
        "cs.created_at::timestamptz > (CURRENT_TIMESTAMP - INTERVAL '7 days')"
    )


def test_bare_now_interval_would_be_mangled():
    # Documents WHY CURRENT_TIMESTAMP is used instead of NOW(): the shim turns
    # bare NOW() into NOW()::text, yielding invalid `text - interval` SQL.
    bad = "SELECT 1 WHERE created_at > NOW() - INTERVAL '7 days'"
    assert "NOW()::text - INTERVAL" in _adapt_params(bad, ())[0]


def _as_dt(s: str) -> datetime:
    """Parse an ISO or space-separated timestamp, mirroring text::timestamptz.

    Python 3.10's fromisoformat needs a full ±HH:MM offset (3.11 accepts the
    bare '+00' Postgres renders); normalize both forms so this runs on the box.
    """
    s = s.replace("Z", "+00:00")
    if len(s) >= 3 and (s[-3] in "+-") and s[-2:].isdigit():
        s += ":00"
    return datetime.fromisoformat(s)


def test_iso_created_at_boundary_temporal_vs_lexical():
    """An app-written ISO 'YYYY-MM-DDTHH:MM:SSZ' row near the interval boundary
    is ordered correctly by a temporal compare, but WRONGLY by the old lexical
    text compare — pinning why the ::timestamptz cast is required."""
    # CURRENT_TIMESTAMP::text renders space-separated (the old threshold form).
    threshold = "2026-06-21 12:00:00+00"
    older_iso = "2026-06-21T08:00:00Z"   # truly BEFORE threshold → excluded
    newer_iso = "2026-06-21T18:00:00Z"   # truly AFTER threshold  → included

    # Real temporal comparison (what created_at::timestamptz > threshold does):
    assert _as_dt(newer_iso) > _as_dt(threshold)
    assert not (_as_dt(older_iso) > _as_dt(threshold))

    # OLD lexical text comparison mis-orders the older ISO row as NEWER, because
    # 'T' (0x54) > ' ' (0x20): it would wrongly include a too-old session.
    assert older_iso > threshold  # the WRONG verdict the fix eliminates


@pytest.mark.skipif(not os.environ.get("POSTGRES_URL"), reason="needs live Postgres")
@pytest.mark.asyncio
async def test_timestamptz_cast_is_format_robust_live():
    """On a real Postgres (Jetson runner): created_at::timestamptz parses BOTH
    the ISO 'T...Z' and space-separated forms and orders them correctly around
    a 7-day interval boundary."""
    import asyncpg

    conn = await asyncpg.connect(os.environ["POSTGRES_URL"])
    try:
        now = datetime.now(timezone.utc)
        inside_iso = (now - timedelta(days=7) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        outside_iso = (now - timedelta(days=7) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Space-separated form (schema NOW()::text style) at the same inside instant.
        inside_space = (now - timedelta(days=7) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S+00")

        q = "SELECT ($1::text)::timestamptz > (CURRENT_TIMESTAMP - INTERVAL '7 days')"
        assert await conn.fetchval(q, inside_iso) is True
        assert await conn.fetchval(q, outside_iso) is False
        assert await conn.fetchval(q, inside_space) is True  # both formats parse
    finally:
        await conn.close()
