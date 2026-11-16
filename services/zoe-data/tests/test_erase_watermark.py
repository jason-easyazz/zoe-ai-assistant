"""A member erase leaves a durable WATERMARK, and no transcript miner re-derives the erased member's facts from the surviving turns.

The bug (seen live 2026-11-14 with a synthetic demo member): the turn digest's late write was dropped by the in-memory erase gate
(#2569), but about three minutes later the idle-consolidation sweep - which STARTED after the erase, so its snapshot was fresh - read
the session's turns from ``chat_messages`` (a member erase does not remove the transcript) and stored two rows for the erased
member. The nightly dreaming job runs in another process and cannot see the in-memory gate at all.

The rule (``erase_watermark``): ``delete_user`` records "member X erased at T" in the forget ledger's table BEFORE it erases
anything; every transcript miner drops that member's turns at or before T. Each scenario runs the REAL ``delete_user`` against a fake
collection and has a CONTROL with the watermark check replaced by a pass-through, showing the row landing. Synthetic text only.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import itertools
import json
import logging

import aiosqlite
import pytest

import db_pool
import erase_watermark as ew
import memory_digest
import memory_forgotten
import memory_idle_consolidation as mic
import memory_service
from forgotten_support import migration_sql
from test_erase_outruns_digest import OTHER, _erase, _rows, env  # noqa: F401 - the real delete_user over a fake collection
from test_memory_digest_row_log import FACT, T1
from test_memory_idle_consolidation import _ctx_factory, _FakeConn, _Row
from test_memory_implicit_supersede import UID
from test_memory_open_loops_extract import _install, _LoopsDb, _loop

pytestmark = pytest.mark.ci_safe

UTC = dt.timezone.utc
MORE = "We also spent the weekend repainting the back fence a pale blue colour and planting some tomatoes by the shed."


def _no_check(monkeypatch):
    """The CONTROL: the watermark filter replaced by a pass-through (what every miner did before this change)."""
    async def passthrough(rows, **_kw):
        return list(rows)
    monkeypatch.setattr(ew, "keep_after", passthrough)


def _now():
    return dt.datetime.now(UTC)


class _Down:
    """A watermark store that cannot be read (or written)."""

    async def get(self, _uid):
        raise ConnectionError("store down")

    async def put(self, *_a):
        raise ConnectionError("store down")


def _idle(user, turns):
    conn = _FakeConn(turns)
    return conn, mic.consolidate_session("s-1", user, get_ctx=_ctx_factory(conn))


def _session(at, user_text=T1):
    return [_Row(role="user", content=user_text, metadata=None, at=at),
            _Row(role="assistant", content="That sounds hard. I'm sorry.", metadata=None, at=at + dt.timedelta(seconds=2))]


# ── the live sequence: turn stored, member erased, THEN an idle sweep ────────────────────────────────────────────────────────

@pytest.mark.parametrize("check", [True, False], ids=["watermark", "control-no-watermark-check"])
def test_an_idle_sweep_after_the_erase_stores_nothing_from_the_surviving_transcript(env, monkeypatch, check):
    svc, col = env
    if not check:
        _no_check(monkeypatch)
    said = _now() - dt.timedelta(minutes=3)

    async def go():
        await _erase(svc)                    # the turn is already in chat_messages; the erase does not touch the transcript
        conn, sweep = _idle(UID, _session(said))
        return conn, await sweep             # the sweep starts AFTER the erase: the in-memory gate's snapshot is fresh

    conn, stored = asyncio.run(go())
    if not check:
        assert stored == 1 and [d for d, _ in _rows(col, UID)] == [FACT], "control: the surviving turn is re-mined (the live bug)"
        return
    assert stored == 0 and _rows(col, UID) == []
    assert conn.executed, "the consolidation state still advances: the skipped turns stay skipped"


def test_a_turn_after_the_erase_is_mined_normally(env):
    svc, col = env

    async def go():
        await _erase(svc)
        conn, sweep = _idle(UID, _session(_now() + dt.timedelta(seconds=1)))
        return await sweep

    assert asyncio.run(go()) == 1 and [d for d, _ in _rows(col, UID)] == [FACT]


def test_another_members_session_is_unaffected(env):
    svc, col = env

    async def go():
        await _erase(svc, UID)
        conn, sweep = _idle(OTHER, _session(_now() - dt.timedelta(minutes=3)))
        return await sweep

    assert asyncio.run(go()) == 1 and [d for d, _ in _rows(col, OTHER)] == [FACT]


def test_an_unreadable_watermark_defers_the_idle_session_without_moving_its_state(env, caplog):
    svc, col = env
    caplog.set_level(logging.INFO)
    ew.set_backend(_Down())
    conn, sweep = _idle(UID, _session(_now() - dt.timedelta(minutes=3)))
    assert asyncio.run(sweep) == 0
    assert _rows(col, UID) == [] and conn.executed == [], "nothing mined, the session is re-read next sweep"
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("ERASE_WATERMARK_UNREADABLE")]
    assert lines == ["ERASE_WATERMARK_UNREADABLE reader=idle_consolidation code=ConnectionError strict=1"]
    assert all(UID not in r.getMessage() and T1 not in r.getMessage() for r in caplog.records if "ERASE_WATERMARK" in r.getMessage())


# ── the nightly digest (and so its emotional pass, the night mind and conversation notes, which read the same loader) ─────────

class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _DayDb:
    """The digest's day read: ``(content, id, created_at, metadata)`` rows, as the SQL returns them (Postgres text times)."""

    def __init__(self, rows):
        self.rows = rows

    async def execute(self, sql, params=()):
        return _Cur(self.rows if "FROM chat_messages" in sql else [])


def _pg(at):
    """``NOW()::TEXT`` shape in Zoe's zone: ``2026-11-14 09:00:00.12345+08`` (trailing zeros trimmed, hour-only offset)."""
    s = at.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")
    return s + "+08"


@pytest.mark.parametrize("check", [True, False], ids=["watermark", "control-no-watermark-check"])
def test_the_nightly_digest_after_the_erase_mines_nothing_of_the_erased_member(env, monkeypatch, check):
    svc, col = env
    if not check:
        _no_check(monkeypatch)

    async def none(*_a, **_k):
        return 0

    async def no(*_a, **_k):
        return False

    monkeypatch.setattr(memory_digest, "_emotional_memory_pass", none)
    monkeypatch.setattr(memory_digest, "_is_contradiction", no)
    said = _now() - dt.timedelta(hours=2)
    rows = [(T1, "m-1", _pg(said), None), (MORE, "m-2", _pg(said + dt.timedelta(seconds=30)), None)]

    async def go():
        await _erase(svc)
        return await memory_digest.run_memory_digest(UID, db=_DayDb(rows))

    result = asyncio.run(go())
    if not check:
        assert FACT in [d for d, _ in _rows(col, UID)], "control: the night re-mines the erased member's day"
        return
    assert result.get("skipped_reason") == "insufficient_activity" and _rows(col, UID) == []


def test_the_digest_loader_fails_closed_on_an_unreadable_watermark(env):
    ew.set_backend(_Down())
    rows = [(T1, "m-1", _pg(_now()), None)]
    with pytest.raises(memory_digest.TranscriptLoadError):
        asyncio.run(memory_digest._load_todays_messages(UID, _DayDb(rows)))


# ── open loops (the dreaming cycle's transcript read, in the night process) ──────────────────────────────────────────────────

LOOP_TURN = "The plumber still has not sent the quote for the kitchen sink"


@pytest.mark.parametrize("check", [True, False], ids=["watermark", "control-no-watermark-check"])
def test_open_loops_after_the_erase_insert_nothing(env, monkeypatch, check):
    svc, _col = env
    if not check:
        _no_check(monkeypatch)
    asyncio.run(_erase(svc))                 # the real erase first (its own stores), then the night's loop pass over the transcript
    db = _LoopsDb([(LOOP_TURN, _pg(_now() - dt.timedelta(hours=5)))])
    _install(monkeypatch, db, reply=json.dumps([_loop("User is waiting on a quote from the plumber")]))
    result = asyncio.run(memory_digest._extract_open_loops(UID))
    if not check:
        assert result["inserted"] == 1, "control: the erased member's loop comes back"
        return
    assert (result["inserted"], result["status"]) == (0, "no_messages") and db.inserts == []


# ── the shared filter: exact instant, time zones, fail closed, several members ──────────────────────────────────────────────

W = dt.datetime(2026, 11, 14, 1, 0, 0, 250000, tzinfo=UTC)


def _mark(monkeypatch, user=UID, at=W):
    monkeypatch.setattr(ew, "_now", lambda: at)
    return asyncio.run(ew.record(user, actor="test"))


def _keep(rows, **kw):
    return asyncio.run(ew.keep_after(rows, at=lambda r: r[1], reader="t", **kw))


def test_a_turn_exactly_at_the_watermark_is_skipped_and_one_microsecond_later_is_kept(monkeypatch):
    _mark(monkeypatch)
    rows = [("before", W - dt.timedelta(seconds=1)), ("at", W), ("after", W + dt.timedelta(microseconds=1))]
    assert [r[0] for r in _keep(rows, owner=UID)] == ["after"]


def test_every_time_shape_is_read_on_one_clock(monkeypatch):
    """The same instants as Postgres text in Zoe's zone (+08, trimmed fraction), as ISO Z, as epoch seconds and as a Decimal."""
    from decimal import Decimal
    _mark(monkeypatch)
    at, after = W, W + dt.timedelta(milliseconds=1)
    shapes = [("pg-at", _pg(at)), ("pg-after", _pg(after)), ("z-at", at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")),
              ("epoch-after", after.timestamp()), ("decimal-at", Decimal(str(at.timestamp()))), ("junk", "yesterday"),
              ("none", None), ("offset-colon-after", after.astimezone(dt.timezone(dt.timedelta(hours=-5))).isoformat())]
    assert [r[0] for r in _keep(shapes, owner=UID)] == ["pg-after", "epoch-after", "offset-colon-after"]


def test_a_naive_turn_time_is_kept_only_when_both_readings_are_after_the_watermark(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")          # UTC+8, no daylight saving
    _mark(monkeypatch)
    naive = W.replace(tzinfo=None)
    rows = [("utc-reading-after-local-before", naive + dt.timedelta(hours=1)),
            ("both-after", naive + dt.timedelta(hours=9))]
    assert [r[0] for r in _keep(rows, owner=UID)] == ["both-after"]


def test_only_the_erased_member_is_filtered_and_a_failure_is_per_member_when_not_strict(monkeypatch):
    _mark(monkeypatch)
    rows = [(UID, W), (OTHER, W), ("", W), (UID, W + dt.timedelta(seconds=1))]
    kept = asyncio.run(ew.keep_after(rows, owner=lambda r: r[0], at=lambda r: r[1], reader="t"))
    assert kept == [(OTHER, W), ("", W), (UID, W + dt.timedelta(seconds=1))]
    ew.set_backend(_Down())
    assert asyncio.run(ew.keep_after(rows, owner=lambda r: r[0], at=lambda r: r[1], reader="t", strict=False)) == [("", W)]
    with pytest.raises(ew.Unavailable):
        asyncio.run(ew.keep_after(rows, owner=lambda r: r[0], at=lambda r: r[1], reader="t"))
    assert issubclass(ew.Unavailable, memory_forgotten.LedgerUnavailable), "the ledger readers' existing defer paths apply"


def test_a_later_erase_moves_the_mark_forward_never_back(monkeypatch):
    _mark(monkeypatch, at=W)
    _mark(monkeypatch, at=W - dt.timedelta(hours=1))                # a clock step back must not reopen older turns
    assert asyncio.run(ew.watermark(UID)) == W
    _mark(monkeypatch, at=W + dt.timedelta(hours=1))
    assert asyncio.run(ew.watermark(UID)) == W + dt.timedelta(hours=1)


# ── delete_user writes it first, fail closed ─────────────────────────────────────────────────────────────────────────────────

def test_delete_user_records_the_watermark_and_fails_closed_without_it(env):
    svc, col = env

    async def seed():
        return await svc.ingest("User keeps bees", user_id=UID, source="digest", memory_type="profile", confidence=0.82,
                                status="approved")

    asyncio.run(seed())
    ew.set_backend(_Down())
    with pytest.raises(memory_service.MemoryServiceError, match="erase watermark not recorded"):
        asyncio.run(_erase(svc))
    assert [d for d, _ in _rows(col, UID)] == ["User keeps bees"], "no mark, nothing erased: the caller retries the whole delete"
    be = ew.MemoryBackend()
    ew.set_backend(be)
    before = _now()
    assert asyncio.run(_erase(svc)) == 1 and _rows(col, UID) == []
    assert before <= asyncio.run(ew.watermark(UID)) <= _now() and list(be.rows) == [UID]


# ── durable: survives a restart, and a second, independent reader (the night process) sees it ───────────────────────────────

def test_the_mark_survives_a_restart_is_seen_by_a_second_reader_and_is_invisible_to_the_forget_ledger(monkeypatch, tmp_path):
    path = str(tmp_path / "ledger.db")

    def use(db):
        @contextlib.asynccontextmanager
        async def ctx():
            yield db
        monkeypatch.setattr(db_pool, "get_db_ctx", ctx)

    async def go():
        writer = await aiosqlite.connect(path)
        await writer.executescript(migration_sql("upgrade"))       # the REAL 0038 table: no new migration
        await writer.commit()
        use(writer)
        ew.set_backend(None)                                         # the production (SQL) store
        monkeypatch.setattr(ew, "_now", lambda: W)
        await ew.record(UID, actor="test")
        await writer.close()
        ew.set_backend(None)                                         # "restart": this process's state is gone
        reader = await aiosqlite.connect(path)                      # a fresh connection: stands in for the night process
        use(reader)
        seen = await ew.SqlBackend().get(UID), await ew.watermark(UID), await ew.watermark(OTHER)
        ledger = memory_forgotten.PostgresBackend()
        hidden = await ledger.active_hashes(UID, "2026-11-15T00:00:00Z"), await ledger.users()
        async with reader.execute("SELECT * FROM memory_forgotten") as cur:
            raw = [tuple(r) for r in await cur.fetchall()]
        await reader.close()
        return seen, hidden, raw

    (text, mark, other), (hashes, users), raw = asyncio.run(go())
    assert mark == W and other is None and text == "2026-11-14T01:00:00.250000Z"
    assert hashes == set() and users == [], "an entity-forget reader never sees the mark"
    assert raw == [(UID, ew.KEY, "erase", "test", "2026-11-14T01:00:00.250000Z", ew.NEVER)], "an id and a time: no text"


class _FailSecond(ew.MemoryBackend):
    """The first write (start of the erase) lands; the second (end of the erase) fails."""

    async def put(self, user_id, at, actor):
        if self.rows.get(user_id):
            raise ConnectionError("store down")
        await super().put(user_id, at, actor)


@pytest.mark.parametrize("final_ok", [True, False], ids=["final-mark-written", "final-mark-fails"])
def test_a_turn_said_during_the_erase_is_covered_by_the_mandatory_final_mark(env, monkeypatch, final_ok):
    """The start mark is BEFORE a turn said while the erase runs; only the end mark covers that turn, so failing to write it must not
    read as "forgotten": delete_user raises (the caller retries) and the start mark still stands."""
    svc, _col = env
    start, end = W, W + dt.timedelta(seconds=2)
    # the first read is the start mark; every later one (the in-flight wait, the late-write checks, the end mark) is ``end``
    clock = itertools.chain([start], itertools.repeat(end))
    monkeypatch.setattr(ew, "_now", lambda: next(clock))
    be = ew.MemoryBackend() if final_ok else _FailSecond()
    ew.set_backend(be)
    during = [("said-during-the-erase", start + dt.timedelta(seconds=1))]
    if not final_ok:
        with pytest.raises(memory_service.MemoryServiceError, match="final erase watermark not recorded"):
            asyncio.run(_erase(svc))
        assert asyncio.run(ew.watermark(UID)) == start, "the start mark still stands for the miners"
        return
    asyncio.run(_erase(svc))
    assert asyncio.run(ew.watermark(UID)) == end
    assert _keep(during, owner=UID) == [], "control: the end mark filters the turn said during the erase"
