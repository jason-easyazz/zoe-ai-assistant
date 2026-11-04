"""Outcome edges (``proactive/outcome_edges.py``, ``ZOE_OUTCOME_EDGES``) - Samantha initiative lane D, E1/E7.

Fixtures only - no household text, no real ids. The DB edge is a real SQLite file built by migrations
0033 + 0036 + 0041 + 0066 behind db_pool's own cursor types (the ``test_proactive_ledger`` harness), so
the idempotent INSERTs, the precedence UPDATE and the selector's own SQL run for real; the two
Postgres-only chat reads of the delivery ledger are replaced by fakes.

Negative controls (each was run red before commit):
  * make ``enabled`` always True                                  -> every flag-off test red;
  * drop the source-rank guard in ``set_outcome``                 -> weaker-source-cannot-overwrite red;
  * ``_TAP_OUTCOME`` maps "neutral" to acted                      -> neutral-tap-decides-nothing red;
  * drop the day-marker insert in ``on_presence``                 -> once-a-day + restart tests red;
  * sort ``rescore`` by input order instead of the total key      -> shuffle-determinism test red;
  * drop ``erase_user``'s DELETE                                  -> forget tests red;
  * apply the re-score to items not due today                     -> not-due-untouched test red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # every edge is stubbed; slim-dep modules only

import contextlib
import importlib.util
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import db_compat
from db_pool import _Cursor, _ExecResult
from proactive import ledger, lines, outcome_edges as oe, pull
from proactive import selector as sel

SVC = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
MEMBER = "member-a"
OTHER = "member-b"
TABLE = "proactive_outcome_edges"


def _migrate(engine, fname, fn="upgrade"):
    spec = importlib.util.spec_from_file_location("mig", SVC / "alembic/versions" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(mod, fn)()
    return mod


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.calls = 0

    def execute(self, sql, params=()):
        self.calls += 1

        async def _run():
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    for fname in ("0033_proactive_candidates.py", "0036_proactive_deliveries.py",
                  "0041_proactive_ledger_lines.py", "0066_outcome_edges.py"):
        _migrate(engine, fname)
    db = _Sqlite(path)
    db.conn.execute(
        "CREATE TABLE open_loops (id INTEGER PRIMARY KEY, user_id TEXT, loop_text TEXT, context TEXT, "
        "follow_up_hint TEXT, emotional_weight INTEGER, created_at TEXT, follow_up_after TEXT, "
        "resolved BOOLEAN DEFAULT FALSE, resolved_at TEXT)")

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "1")
    monkeypatch.setenv("ZOE_PROACTIVE_LEDGER", "1")
    monkeypatch.setenv("ZOE_OUTCOME_EDGES", "on")
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")
    monkeypatch.setenv("ZOE_PULL_NOT_PUSH", "1")
    monkeypatch.delenv("ZOE_DELIVERY_LEDGER", raising=False)
    monkeypatch.delenv("ZOE_SYNTHETIC_USER_ALLOWLIST", raising=False)
    sel._reset_state()
    oe._reset_state()
    state = {"now": NOW, "db": db, "replies": {}, "turns": {}}
    monkeypatch.setattr(sel, "_now", lambda: state["now"])

    async def fake_replies_after(_db, session_id, since, until):
        return sorted(((t, at) for t, at in state["replies"].get(session_id, [])
                       if ledger._parse(since) < at <= ledger._parse(until)), key=lambda r: r[1])[:5]

    async def fake_turns(_db, user_id, start, end, limit=4):
        rows = sorted((at, t) for t, at in state["turns"].get(user_id, [])
                      if ledger._parse(start) < at <= ledger._parse(end))
        return [t for _at, t in rows][:limit]

    monkeypatch.setattr(ledger, "_replies_after", fake_replies_after)
    monkeypatch.setattr(ledger, "_user_turns", fake_turns)
    yield state
    sel._reset_state()
    oe._reset_state()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _loop(env, loop_id, due, *, user=MEMBER, resolved=False, resolved_at=None):
    env["db"].conn.execute(
        "INSERT INTO open_loops (id, user_id, loop_text, context, follow_up_hint, emotional_weight, created_at, "
        "follow_up_after, resolved, resolved_at) VALUES (?, ?, 'x', '', '', 3, ?, ?, ?, ?)",
        (loop_id, user, "2026-09-28 10:00:00", due, resolved, resolved_at))


def _cand(env, ref, kind, sal, *, user=MEMBER, expires=None, cue="interview", surfaced=0):
    env["db"].conn.execute(
        "INSERT INTO proactive_candidates (id, user_id, kind, source_ref, text, hint, salience, on_open, "
        "cue_words, expires_at, surfaced_count, created_at, updated_at) VALUES (?, ?, ?, ?, 'fixture text', "
        "'ask', ?, 1, ?, ?, ?, ?, ?)",
        (f"c-{ref}", user, kind, ref, sal, cue, expires or _iso(NOW + timedelta(hours=30)), surfaced,
         _iso(NOW), _iso(NOW)))


def _edges(env, where="touch_point <> 'presence'", params=()):
    return env["db"].rows(
        f"SELECT user_id, item_id, touch_point, outcome, outcome_source, rescore_rank, score_before, score_after "
        f"FROM {TABLE} WHERE {where} ORDER BY item_id, touch_point", params)


def _sal(env, ref):
    return env["db"].rows("SELECT salience FROM proactive_candidates WHERE source_ref = ?", (ref,))[0][0]


def _seed_due(env):
    """Three open-loop candidates: due later today (A), overdue (B), due in three days (C, not due today)."""
    _loop(env, 1, "2026-09-30 15:00:00")
    _loop(env, 2, "2026-09-29 20:00:00")
    _loop(env, 3, "2026-10-03 09:00:00")
    _cand(env, "open_loops:1", "open_loop", 0.50)
    _cand(env, "open_loops:2", "open_loop", 0.50)
    _cand(env, "open_loops:3", "open_loop", 0.90)


async def _raise_one(env, ref="open_loops:1", sid="s1", cue="interview"):
    """A real raise + settle through the selector for the candidate ``ref`` (greeting shape)."""
    raised = await sel.prepare("Hi Zoe, how are things?", MEMBER, sid)
    assert raised is not None
    assert await sel.settle(raised, produced=True)
    return raised


# -- migration ---------------------------------------------------------------------------------
def test_migration_0066_is_idempotent_chained_and_unique_per_key():
    engine = sa.create_engine("sqlite://")
    mod = _migrate(engine, "0066_outcome_edges.py")
    assert (mod.revision, mod.down_revision) == ("0066", "0065")
    _migrate(engine, "0066_outcome_edges.py")
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({TABLE})")}
        assert cols == {"id", "user_id", "item_id", "kind", "local_day", "touch_point", "outcome", "outcome_at",
                        "outcome_source", "rescore_rank", "score_before", "score_after", "created_at"}
    _migrate(engine, "0066_outcome_edges.py", "downgrade")
    with engine.connect() as conn:
        assert not conn.exec_driver_sql(f"SELECT name FROM sqlite_master WHERE name='{TABLE}'").fetchall()


# -- flag off: nothing written, nothing read ---------------------------------------------------
@pytest.mark.asyncio
async def test_off_writes_nothing_and_touches_no_db(env, monkeypatch):
    monkeypatch.delenv("ZOE_OUTCOME_EDGES", raising=False)
    assert oe.mode() == "off" and not oe.enabled()
    _seed_due(env)
    before = env["db"].calls
    assert await oe.on_presence(MEMBER, NOW) is None
    assert await oe.sweep(now=NOW) == 0
    assert await oe.open_edge(env["db"], user_id=MEMBER, source_ref="open_loops:1", kind="open_loop",
                              touch_point="raise", now=NOW) is False
    assert await oe.set_outcome(env["db"], user_id=MEMBER, source_ref="open_loops:1", outcome="acted",
                                source="tap", now=NOW) == 0
    assert env["db"].calls == before          # not one statement ran
    # the real call sites: a raise, a pull, a tap, a ledger sweep and a selector prepare
    await _raise_one(env)
    await pull.pull(MEMBER, "s2", channel="chat", now=NOW)
    await lines.record_tap(MEMBER, "not_now", now=NOW)
    await ledger.sweep(now=NOW + timedelta(hours=2))
    assert _edges(env, "1=1") == []
    assert [round(_sal(env, f"open_loops:{i}"), 4) for i in (1, 2, 3)] == [0.5, 0.5, 0.9]   # no re-score


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["", "0", "off", "shadow", "enforce", "maybe"])
async def test_only_on_turns_it_on(monkeypatch, raw):
    monkeypatch.setenv("ZOE_OUTCOME_EDGES", raw)
    assert oe.mode() == "off"


# -- outcome transitions -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_raise_opens_an_undecided_edge_and_the_ledger_decides_it(env):
    _loop(env, 1, "2026-09-30 15:00:00")
    _cand(env, "open_loops:1", "open_loop", 0.8)
    raised = await _raise_one(env)
    assert raised.kind == "open_loop"
    assert _edges(env) == [(MEMBER, "open_loops:1", "raise", None, None, None, None, None)]
    env["replies"]["s1"] = [("How did the interview go?", NOW + timedelta(seconds=2))]
    env["turns"][MEMBER] = [("the interview went well thanks", NOW + timedelta(seconds=60))]
    await ledger.sweep(now=NOW + timedelta(minutes=15))
    assert _edges(env) == [(MEMBER, "open_loops:1", "raise", "acted", "ledger_sweep", None, None, None)]


@pytest.mark.asyncio
async def test_an_ignored_raise_is_ignored_and_an_undelivered_one_unknown(env):
    _loop(env, 1, "2026-09-30 15:00:00")
    _cand(env, "open_loops:1", "open_loop", 0.8)
    await _raise_one(env)
    env["replies"]["s1"] = [("How did the interview go?", NOW + timedelta(seconds=2))]
    await ledger.sweep(now=NOW + timedelta(minutes=15))          # no next turn at all
    assert _edges(env)[0][3:5] == ("ignored", "ledger_sweep")
    # a reply that never voiced it -> the ledger says undelivered -> the edge says unknown (never "ignored")
    _cand(env, "open_loops:9", "open_loop", 0.7)
    await oe.open_edge(env["db"], user_id=MEMBER, source_ref="open_loops:9", kind="open_loop",
                       touch_point="raise", now=NOW)
    env["db"].conn.execute(
        "INSERT INTO proactive_deliveries (id, idem_key, user_id, kind, source_ref, shape, delivered_by, session_id, "
        "surfaced_at, expires_at, created_at) VALUES ('d9','k9',?, 'open_loop','open_loops:9','cue','turn','s9',?,?,?)",
        (MEMBER, _iso(NOW), _iso(NOW + timedelta(hours=24)), _iso(NOW)))
    assert await oe.on_ledger_close(env["db"], "d9", "undelivered", NOW) == 1
    assert _edges(env, "item_id = 'open_loops:9'")[0][3:5] == ("unknown", "ledger_sweep")


@pytest.mark.asyncio
async def test_a_pull_is_born_acted_and_a_not_now_tap_turns_it_dismissed(env):
    _loop(env, 1, "2026-09-30 15:00:00")
    _cand(env, "open_loops:1", "open_loop", 0.8)
    res = await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    assert res is not None and res.delivered == 1
    assert _edges(env) == [(MEMBER, "open_loops:1", "pull", "acted", "pull_ask", None, None, None)]
    await lines.record_tap(MEMBER, "neutral", now=NOW + timedelta(seconds=30))
    assert _edges(env)[0][3:5] == ("acted", "pull_ask")           # "that was fine" decides nothing
    await lines.record_tap(MEMBER, "not_now", now=NOW + timedelta(seconds=40))
    assert _edges(env)[0][3:5] == ("dismissed", "tap")
    await lines.record_tap(MEMBER, "welcome", now=NOW + timedelta(seconds=50))   # a later tap replaces the earlier
    assert _edges(env)[0][3:5] == ("acted", "tap")


@pytest.mark.asyncio
async def test_a_weaker_source_never_overwrites_a_stronger_one(env):
    db = env["db"]
    await oe.open_edge(db, user_id=MEMBER, source_ref="open_loops:1", kind="open_loop", touch_point="raise", now=NOW)
    assert await oe.set_outcome(db, user_id=MEMBER, source_ref="open_loops:1", outcome="dismissed",
                                source="tap", now=NOW) == 1
    for weaker in ("loop_resolved", "ledger_sweep", "pull_ask", "expiry"):
        assert await oe.set_outcome(db, user_id=MEMBER, source_ref="open_loops:1", outcome="acted",
                                    source=weaker, now=NOW) == 0
    assert _edges(env)[0][3:5] == ("dismissed", "tap")
    assert await oe.set_outcome(db, user_id=MEMBER, source_ref="open_loops:1", outcome="bogus",
                                source="tap", now=NOW) == 0           # an unknown outcome is refused


@pytest.mark.asyncio
async def test_sweep_resolves_a_raised_loop_and_expires_the_undecided(env):
    db = env["db"]
    _loop(env, 1, "2026-09-30 15:00:00", resolved=True, resolved_at="2026-09-30 09:00:00")
    _loop(env, 2, "2026-09-30 15:00:00", resolved=True, resolved_at="2026-09-29 09:00:00")   # resolved BEFORE the raise
    _loop(env, 3, "2026-09-30 15:00:00")
    for i in (1, 2, 3):
        await oe.open_edge(db, user_id=MEMBER, source_ref=f"open_loops:{i}", kind="open_loop",
                           touch_point="raise", now=NOW)
    assert await oe.sweep(now=NOW + timedelta(hours=2)) == 1          # loop 1 only; 3 is not 24 h old yet
    got = {r[1]: r[3:5] for r in _edges(env)}
    assert got["open_loops:1"] == ("acted", "loop_resolved")
    assert got["open_loops:2"] == (None, None) and got["open_loops:3"] == (None, None)
    await oe.sweep(now=NOW + timedelta(hours=25))
    got = {r[1]: r[3:5] for r in _edges(env)}
    assert got["open_loops:2"] == ("unknown", "expiry") and got["open_loops:3"] == ("unknown", "expiry")
    assert got["open_loops:1"] == ("acted", "loop_resolved")          # a decided edge is never expired over


# -- the first-presence re-score ---------------------------------------------------------------
def _due(ref, base, hour=15, kind="open_loop"):
    return oe.Due(f"c-{ref}", ref, kind, base, datetime(2026, 9, 30, hour, tzinfo=timezone.utc))


def test_rescore_order_is_total_and_independent_of_input_order():
    items = [_due("open_loops:1", 0.5), _due("open_loops:2", 0.5), _due("open_loops:3", 0.5, hour=9),
             _due("open_loops:4", 0.9), _due("open_loops:5", 0.2), _due("events:7", 0.5, hour=9, kind="event")]
    want = oe.rescore(items, {"open_loops:5": (0, 0)})
    # equal scores: the earlier due time first, then the ref
    assert [s.source_ref for s in want] == ["open_loops:4", "events:7", "open_loops:3", "open_loops:1",
                                            "open_loops:2", "open_loops:5"]
    assert [s.rank for s in want] == [1, 2, 3, 4, 5, 6]
    rng = random.Random(7)
    for _ in range(20):
        shuffled = items[:]
        rng.shuffle(shuffled)
        assert oe.rescore(shuffled, {"open_loops:5": (0, 0)}) == want


def test_rescore_arithmetic_boost_cap_and_outcome_factors():
    out = {s.source_ref: s.after for s in oe.rescore(
        [_due("a", 0.5), _due("b", 0.5), _due("c", 0.5), _due("d", 0.5), _due("e", 0.95)],
        {"b": (1, 0), "c": (0, 2), "d": (0, 9)})}
    assert out["a"] == 0.7                       # 0.5 + 0.2
    assert out["b"] == 0.45                      # dismissed: 0.5 * 0.5 + 0.2
    assert out["c"] == round(0.5 * 0.85 ** 2 + 0.2, 4)
    assert out["d"] == round(0.5 * 0.85 ** 3 + 0.2, 4)     # ignored count capped at 3
    assert out["e"] == 1.0                       # capped
    assert oe.rescore([], {}) == []


@pytest.mark.asyncio
async def test_presence_rescores_only_todays_due_items_once_and_records_the_edges(env):
    _seed_due(env)
    order = await oe.on_presence(MEMBER, NOW)
    assert order == ["open_loops:2", "open_loops:1"]          # overdue (earlier due) ahead of later-today at a tie
    assert _sal(env, "open_loops:1") == 0.7 and _sal(env, "open_loops:2") == 0.7
    assert _sal(env, "open_loops:3") == 0.9                   # due in three days: untouched
    got = {r[1]: r for r in _edges(env, "1=1")}
    assert got["open_loops:2"][2:] == ("presence", None, None, 1, 0.5, 0.7)
    assert got["open_loops:1"][5:] == (2, 0.5, 0.7)
    assert "open_loops:3" not in got and oe.DAY_MARKER in got
    # once per member per local day: a second turn, and a "restart" (the in-process memo is gone), re-score nothing
    assert await oe.on_presence(MEMBER, NOW + timedelta(hours=1)) is None
    oe._reset_state()
    assert await oe.on_presence(MEMBER, NOW + timedelta(hours=2)) is None
    assert _sal(env, "open_loops:1") == 0.7                   # not boosted twice
    # tomorrow it runs again; another member has their own day
    assert await oe.on_presence(OTHER, NOW) == []
    _cand(env, "open_loops:4", "open_loop", 0.5, expires=_iso(NOW + timedelta(days=3)))
    _loop(env, 4, "2026-10-01 11:00:00")
    # (the two overdue items are still pending, so they are due again and climb: an overdue item only gets more urgent)
    assert await oe.on_presence(MEMBER, NOW + timedelta(days=1)) == ["open_loops:2", "open_loops:1", "open_loops:4"]
    assert _sal(env, "open_loops:1") == 0.9 and _sal(env, "open_loops:4") == 0.7


@pytest.mark.asyncio
async def test_a_dismissed_item_ranks_below_an_untouched_one_at_presence(env):
    _loop(env, 1, "2026-09-30 15:00:00")
    _loop(env, 2, "2026-09-30 15:00:00")
    _cand(env, "open_loops:1", "open_loop", 0.5)
    _cand(env, "open_loops:2", "open_loop", 0.5)
    # yesterday the member dismissed item 1 (a real, tapped edge)
    yday = NOW - timedelta(days=1)
    await oe.open_edge(env["db"], user_id=MEMBER, source_ref="open_loops:1", kind="open_loop",
                       touch_point="raise", now=yday)
    await oe.set_outcome(env["db"], user_id=MEMBER, source_ref="open_loops:1", outcome="dismissed",
                         source="tap", now=yday, day=oe._day(yday))
    assert await oe.on_presence(MEMBER, NOW) == ["open_loops:2", "open_loops:1"]
    assert _sal(env, "open_loops:1") == 0.45 and _sal(env, "open_loops:2") == 0.7


@pytest.mark.asyncio
async def test_the_rescored_item_leads_the_same_turns_raise_pick(env):
    _loop(env, 1, "2026-09-30 15:00:00")
    _cand(env, "open_loops:1", "open_loop", 0.45)
    _cand(env, "open_loops:3", "open_loop", 0.6, cue="holiday")      # not due: keeps 0.6, and would win unboosted
    _loop(env, 3, "2026-10-04 09:00:00")
    raised = await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1")   # first turn of the day -> re-score first
    assert raised is not None and raised.candidate_id == "c-open_loops:1"   # 0.45 + 0.2 = 0.65 > 0.6
    assert _sal(env, "open_loops:1") == 0.65 and _sal(env, "open_loops:3") == 0.6


@pytest.mark.asyncio
async def test_guests_and_the_marker_failure_path(env, monkeypatch):
    _seed_due(env)
    assert await sel.prepare("Hi Zoe", "guest", "sg") is None           # a guest never reaches the re-score
    assert _edges(env, "1=1") == []

    async def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(oe, "_due_today", boom)
    assert await oe.on_presence(MEMBER, NOW) is None                    # never raises
    assert _edges(env, "1=1") == []                                     # the marker was rolled back: a later turn retries


# -- forget ------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_erase_user_removes_every_edge_of_the_member_only_and_whatever_the_flag(env, monkeypatch):
    _seed_due(env)
    await oe.on_presence(MEMBER, NOW)
    await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    await oe.open_edge(env["db"], user_id=OTHER, source_ref="open_loops:77", kind="open_loop",
                       touch_point="raise", now=NOW)
    assert _edges(env, "user_id = ?", (MEMBER,))
    monkeypatch.delenv("ZOE_OUTCOME_EDGES")                             # forget does not depend on the flag
    assert await oe.erase_user(MEMBER) >= 3                              # presence rows + the marker + the pull edge
    assert _edges(env, "user_id = ?", (MEMBER,)) == []
    assert len(_edges(env, "user_id = ?", (OTHER,))) == 1               # nobody else's
    assert await oe.erase_user("") == 0


@pytest.mark.asyncio
async def test_the_table_can_hold_no_words(env):
    # no column can hold text: ids, labels, numbers and stamps only
    names = {r[1] for r in env["db"].rows(f"PRAGMA table_info({TABLE})")}
    assert not names & {"text", "hint", "cue_words", "message", "utterance", "name"}


def test_the_member_forget_path_calls_it_and_the_inventory_lists_it():
    import ast
    import forget_reach

    store = {s.key: s for s in forget_reach.STORES}[TABLE]
    assert store.status == forget_reach.REACHED and store.member == "outcome_edges.erase_user"
    tree = ast.parse((SVC / "memory_service.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "delete_user")
    assert any(isinstance(n, ast.Attribute) and n.attr == "erase_user" and isinstance(n.value, ast.Name)
               and n.value.id == "outcome_edges" for n in ast.walk(fn))


# -- privacy -----------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_logs_carry_no_item_text(env, caplog):
    import logging

    _loop(env, 1, "2026-09-30 15:00:00")
    _cand(env, "open_loops:1", "open_loop", 0.8)
    with caplog.at_level(logging.DEBUG):
        await oe.on_presence(MEMBER, NOW)
        await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
        await lines.record_tap(MEMBER, "not_now", now=NOW)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "fixture text" not in logged and "OUTCOME_" in logged
