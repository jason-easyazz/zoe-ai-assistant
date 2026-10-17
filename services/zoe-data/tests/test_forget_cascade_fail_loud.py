"""A forget that cannot reach a derived store must say so - never read as done (P1, "forgotten means forever").

Measured 2026-10-13: ``~/.zoe-logs/zoe-data.app.log`` held 355 lines of ``memory_forget_cascade: no database (RuntimeError) - derived
stores not cleared`` (+ commitments / omi_recordings ``NOT erased``). They came from pytest runs on the CI runner (the lifespan a
TestClient enters attached the app-log handler to the operator's real log dir), not from the live server - whose pool exists the
moment it serves. But the same lines showed the live code's shape: ``cascade_forget`` swallowed a missing/lost/exhausted pool at
WARNING and ``memory_forget_entity`` went on to say "Okay - I've forgotten ...". So a real pool failure on a real forget would have
left the portrait, the card, open loops, the contact and the commitments naming the person while Zoe said it was done.

Contract pinned here:
  * a derived store that cannot be cleared (after one retry) is in ``CascadeResult.failed`` and logged at ERROR;
  * ``memory_forget_entity`` then answers "can't say it's forgotten yet ... tell me again" (the idempotent redo), never "forgotten";
  * a process that had a pool and LOST it (or whose pool is exhausted) is a failure; a process that NEVER had one (lab, bare test,
    script) has nothing to clear and is not;
  * the stores reached through their own modules (restraint mutes, commitments, omi) raise under ``strict=True`` so the cascade sees them.

Every claim has its break-the-fix control (see each test's last lines).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3

import pytest

import db_pool
import forget_redact
import intent_router
import memory_forget_cascade as cascade
from forgotten_support import (  # noqa: F401 - fixtures
    FRIEND, USER, ledger_env, no_offers, open_forgotten_db, svc, use_db,
)
from test_forget_durable_ledger import _alive, _forget, _seed_derived, _teach

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    monkeypatch.setattr(cascade, "RETRY_DELAY_S", 0)


class _NoPool:
    """``db_pool.get_db_ctx`` of a process whose pool is gone (or never was): get_pool() raises RuntimeError."""

    def __init__(self, exc: Exception):
        self.exc = exc

    def __call__(self):
        exc = self.exc

        @contextlib.asynccontextmanager
        async def ctx():
            raise exc
            yield  # pragma: no cover
        return ctx()


def _lost_pool(monkeypatch, *, ever_initialised: bool, exc: Exception | None = None):
    monkeypatch.setattr(db_pool, "get_db_ctx", _NoPool(exc or RuntimeError("db_pool not initialised - call init_pool() first")))
    monkeypatch.setattr(db_pool, "_ever_initialised", ever_initialised)


class _FlakyDb:
    """The real in-memory DB, except a statement containing ``marker`` raises (a blip / lock on ONE store)."""

    __module__ = "aiosqlite.flaky"   # forget_redact picks its SQLite owner expression by the connection's module

    def __init__(self, db, marker: str, *, times: int | None = None):
        self._db, self.marker, self.times, self.hits = db, marker, times, 0

    def execute(self, sql, *params):
        if self.marker in sql and (self.times is None or self.hits < self.times):
            self.hits += 1
            raise sqlite3.OperationalError("database is locked")
        return self._db.execute(sql, *params)

    def __getattr__(self, name):
        return getattr(self._db, name)


# -- the cascade itself --------------------------------------------------------

@pytest.mark.asyncio
async def test_a_lost_pool_is_a_failure_in_every_store(monkeypatch, caplog):
    _lost_pool(monkeypatch, ever_initialised=True)
    with caplog.at_level(logging.ERROR):
        res = await cascade.cascade_forget(USER, FRIEND)
    assert not res.complete
    assert set(res.failed) == {"database", "restraint_mutes", "commitments", "omi_recordings", "omi_day_review"}
    assert any("FORGET_CASCADE_INCOMPLETE" in r.getMessage() and USER in r.getMessage() for r in caplog.records)
    assert FRIEND not in caplog.text            # counts / labels only, never the name
    # control: the SAME failure in a process that never had a pool is "nothing to clear", not a failure
    _lost_pool(monkeypatch, ever_initialised=False)
    assert (await cascade.cascade_forget(USER, FRIEND)).complete


@pytest.mark.asyncio
async def test_an_exhausted_pool_is_a_failure(monkeypatch):
    _lost_pool(monkeypatch, ever_initialised=True, exc=db_pool.PoolExhaustedError("pool exhausted"))
    res = await cascade.cascade_forget(USER, FRIEND)
    assert "database" in res.failed and not res.complete


@pytest.mark.asyncio
async def test_a_failed_store_is_recorded_the_others_still_run_and_the_step_is_retried(monkeypatch, no_offers):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        use_db(monkeypatch, db)                              # the strict erasers (restraint / commitments / omi) read this DB too
        flaky = _FlakyDb(db, "FROM user_portraits")          # always fails
        res = await cascade.cascade_forget(USER, FRIEND, db=flaky)
        assert res.failed == ["portrait"] and not res.complete
        assert flaky.hits == 2                               # tried, retried once, then recorded
        assert res.card == 1 and res.people == 1 and res.loops == 1      # the other stores were not blocked
        assert await _alive(db, "user_portraits", f"user_id = '{USER}'") == 1   # and the portrait really is still there

        # a blip that clears on the retry is NOT a failure
        once = _FlakyDb(db, "FROM user_portraits", times=1)
        res = await cascade.cascade_forget(USER, FRIEND, db=once)
        assert res.complete and res.portrait == 1 and once.hits == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_missing_table_is_nothing_to_clear_not_a_failure():
    db = await open_forgotten_db()
    try:
        await db.execute("DROP TABLE user_portraits")
        res = await cascade.cascade_forget(USER, FRIEND, db=db)
        assert res.complete
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_strict_erasers_raise_and_the_default_still_swallows(monkeypatch):
    import commitments
    import omi_day_review
    import omi_recordings
    import restraint
    _lost_pool(monkeypatch, ever_initialised=True)
    rx = cascade._name_re(FRIEND)
    for call in (lambda **k: commitments.erase_entity(USER, rx, **k),
                 lambda **k: omi_recordings.erase_entity(USER, rx, **k),
                 lambda **k: omi_day_review.erase_entity(USER, rx, **k),
                 lambda **k: restraint.erase_entity(USER, FRIEND, **k)):
        assert await call() == 0                              # default: swallow + log (unchanged for other callers)
        with pytest.raises(RuntimeError):
            await call(strict=True)


# -- the live forget path ------------------------------------------------------

@pytest.mark.asyncio
async def test_forget_with_a_failing_derived_store_does_not_say_forgotten(svc, monkeypatch, no_offers):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)
        await _teach(svc)
        use_db(monkeypatch, _FlakyDb(db, "FROM user_portraits"))
        reply = await _forget()
        assert "can't say it's forgotten yet" in reply and "tell me again" in reply
        assert "I've forgotten" not in reply and "I've also" not in reply
        assert await _alive(db, "user_portraits", f"user_id = '{USER}'") == 1     # the honest answer: it is still there
        assert await _alive(db, "people", "id = 'p-dana' AND deleted = 1") == 1   # the stores that could be reached were

        # saying it again once the store is healthy redoes the whole (idempotent) forget and confirms it
        use_db(monkeypatch, db)
        reply = await _forget()
        assert "can't say" not in reply and "cleared them from your summary" in reply   # (the memory rows went the first time)
        assert await _alive(db, "user_portraits", f"user_id = '{USER}'") == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_forget_when_nothing_matched_still_reports_a_failed_cascade(svc, monkeypatch, no_offers):
    db = await open_forgotten_db()
    try:
        await _seed_derived(db)                       # derived text names Dana but no memory row does
        use_db(monkeypatch, _FlakyDb(db, "FROM user_portraits"))
        reply = await _forget()
        assert "can't say it's forgotten yet" in reply and "I don't have anything saved" not in reply
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_forget_on_a_process_that_lost_its_pool_is_loud(svc, monkeypatch, no_offers):
    await _teach(svc)
    _lost_pool(monkeypatch, ever_initialised=True)
    reply = await _forget()
    assert "can't say it's forgotten yet" in reply
    assert "I've forgotten" not in reply
    # control (the pre-fix behaviour): a process that never had a pool has nothing to redact or clear and forgets as before
    await _teach(svc)
    _lost_pool(monkeypatch, ever_initialised=False)
    assert "forgotten 1 thing about Dana" in await _forget()


@pytest.mark.asyncio
async def test_transcript_redaction_raises_on_a_lost_pool_and_is_quiet_without_one(monkeypatch):
    _lost_pool(monkeypatch, ever_initialised=True)
    with pytest.raises(RuntimeError):
        await forget_redact.redact_transcripts(USER, FRIEND)
    _lost_pool(monkeypatch, ever_initialised=False)
    assert await forget_redact.redact_transcripts(USER, FRIEND) == (0, 0)


def test_the_cascade_note_reports_an_unexpected_cascade_exception_as_incomplete(monkeypatch):
    async def boom(*a, **k):
        raise ValueError("x")

    monkeypatch.setattr(cascade, "cascade_forget", boom)
    note, ok = asyncio.run(intent_router._forget_cascade_note(USER, FRIEND))
    assert (note, ok) == ("", False)
