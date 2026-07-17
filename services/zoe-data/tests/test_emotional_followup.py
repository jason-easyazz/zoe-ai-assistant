"""Emotional follow-up trigger — Samantha proactivity (VISION pillar 3).

Covers qualification (valence/intensity/age), the anti-nag guards (one-per-
moment-ever + one-per-user-per-day), waking-hours gate, and the flag-OFF no-op.
Everything synthetic: a fake MemoryService (emotional_moment rows) + a fake DB
cursor + a frozen clock, so no store / no model / no Postgres.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import memory_service
import proactive.triggers.emotional_followup as efu
from proactive.triggers.emotional_followup import EmotionalFollowUpTrigger

pytestmark = pytest.mark.ci_safe

# A fixed "now" in a waking hour (14:00 local).
_NOW = datetime(2026, 7, 5, 14, 0, tzinfo=efu._ZOE_TZ)


class _FrozenNow(datetime):
    @classmethod
    def now(cls, tz=None):
        return _NOW.astimezone(tz) if tz else _NOW


def _moment(mem_id, text, *, valence, intensity, age_h):
    added = (_NOW.astimezone(timezone.utc) - timedelta(hours=age_h)).isoformat()
    return SimpleNamespace(id=mem_id, text=text, metadata={
        "memory_type": "emotional_moment", "candidate_valence": valence,
        "candidate_intensity": intensity, "added_at": added,
    })


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        return False
    async def fetchone(self):
        return self._rows[0] if self._rows else None
    def __aiter__(self):
        self._it = iter(self._rows)
        return self
    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeDB:
    """Dispatches the trigger's three queries by SQL shape. `fired_today` =
    set of user_ids already sent a follow-up today; `followed` = set of item_ids
    ever followed up."""
    def __init__(self, *, users, fired_today=(), followed=()):
        self.users = list(users)
        self.fired_today = set(fired_today)
        self.followed = set(followed)

    def execute(self, sql, params=()):
        s = sql.lower()
        if "from chat_sessions" in s:
            return _FakeCursor([(u,) for u in self.users])
        if "proactive_pending" in s and "created_at::date = current_date" in s:
            user_id = params[1]
            return _FakeCursor([(1,)] if user_id in self.fired_today else [])
        if "proactive_pending" in s and "item_id=?" in s:
            item_id = params[1]
            return _FakeCursor([(1,)] if item_id in self.followed else [])
        raise AssertionError(f"unexpected SQL: {sql}")


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(efu, "datetime", _FrozenNow)
    monkeypatch.setenv("ZOE_EMOTIONAL_FOLLOWUP_ENABLED", "1")

    def _set_moments(moments):
        svc = SimpleNamespace()
        async def _load(user_id, *, limit=20):
            return moments
        svc.load_for_prompt = _load
        monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    return _set_moments


async def _run(db):
    return await EmotionalFollowUpTrigger().check(db)


# ── qualification + composition ───────────────────────────────────────────────

async def test_qualifying_worry_yields_one_followup(wired):
    wired([_moment("m1", "Jason has been anxious about the house settlement",
                   valence="neg", intensity=0.9, age_h=26)])
    res = await _run(_FakeDB(users=["jason"]))
    assert len(res) == 1
    r = res[0]
    assert r.item_id == "m1"                       # per-moment dedup key
    assert r.trigger_type == "emotional_followup"
    assert r.context["fact"].startswith("Jason has been anxious")
    assert "house settlement" in r.message         # topic surfaced in fallback
    assert "[mem:" not in r.message                # no citation ids leaked


async def test_highest_intensity_moment_wins(wired):
    wired([
        _moment("low", "Jason has been a bit annoyed about traffic", valence="neg", intensity=0.65, age_h=30),
        _moment("high", "Jason is scared his dad is in hospital", valence="neg", intensity=0.95, age_h=30),
    ])
    res = await _run(_FakeDB(users=["jason"]))
    assert len(res) == 1 and res[0].item_id == "high"


# ── qualification filters (each must suppress) ────────────────────────────────

@pytest.mark.parametrize("mid,valence,intensity,age_h", [
    ("pos", "pos", 0.9, 30),          # positive valence — not a worry
    ("mild", "neg", 0.3, 30),         # below the intensity floor
    ("fresh", "neg", 0.9, 2),         # too fresh (same-day pile-on)
    ("stale", "neg", 0.9, 24 * 10),   # too old to still matter
])
async def test_non_qualifying_moment_is_skipped(wired, mid, valence, intensity, age_h):
    wired([_moment(mid, "Jason has been anxious about the move", valence=valence, intensity=intensity, age_h=age_h)])
    assert await _run(_FakeDB(users=["jason"])) == []


# ── anti-nag guards ───────────────────────────────────────────────────────────

async def test_per_moment_dedup_never_refollows(wired):
    wired([_moment("m1", "Jason has been anxious about the settlement", valence="neg", intensity=0.9, age_h=26)])
    res = await _run(_FakeDB(users=["jason"], followed={"m1"}))
    assert res == []


async def test_daily_cap_one_per_user(wired):
    wired([_moment("m1", "Jason has been anxious about the settlement", valence="neg", intensity=0.9, age_h=26)])
    res = await _run(_FakeDB(users=["jason"], fired_today={"jason"}))
    assert res == []


# ── gates ─────────────────────────────────────────────────────────────────────

async def test_flag_off_is_a_true_noop(monkeypatch):
    # Deliberately NOT freezing the clock: the flag check must short-circuit
    # BEFORE datetime.now() is reached. If a refactor ever moved the clock read
    # ahead of the flag gate, this test would hit the live clock and expose it.
    monkeypatch.delenv("ZOE_EMOTIONAL_FOLLOWUP_ENABLED", raising=False)

    class _Boom:
        def execute(self, *a, **k):
            raise AssertionError("DB must not be touched when the flag is off")
    assert await EmotionalFollowUpTrigger().check(_Boom()) == []


async def test_outside_waking_hours_is_skipped(monkeypatch, wired):
    # Re-freeze to 03:00 local — before the waking window.
    night = datetime(2026, 7, 5, 3, 0, tzinfo=efu._ZOE_TZ)

    class _Night(datetime):
        @classmethod
        def now(cls, tz=None):
            return night.astimezone(tz) if tz else night
    monkeypatch.setattr(efu, "datetime", _Night)
    wired([_moment("m1", "Jason has been anxious about the settlement", valence="neg", intensity=0.9, age_h=26)])

    class _Boom:
        def execute(self, *a, **k):
            raise AssertionError("must not query outside waking hours")
    assert await EmotionalFollowUpTrigger().check(_Boom()) == []
