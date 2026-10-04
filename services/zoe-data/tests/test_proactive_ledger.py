"""Delivery ledger (``proactive/ledger.py``, ``ZOE_PROACTIVE_LEDGER``) — pull-not-push inbox PR 1.

Fixtures only — no household text, no real ids. The DB edge is a real SQLite file built by
migrations 0033 + 0035 behind db_pool's own cursor types, so the idempotent INSERT, the
outcome UPDATE and the selector's own SQL run for real; the Postgres-only chat-turn read
(``ledger._user_turns``) is replaced by a fake, like every other ``ci_safe`` test of a
Postgres edge.

Negative controls (each was run red before commit):
  * flag off  -> no row, no sweep DB access, the lanes' settle calls carry NO ``reply``
    kwarg, and the candidate table + raise block are byte-identical to a flag-on run;
  * drop ``idem_key``'s UNIQUE from migration 0035               -> duplicate-key test red;
  * make ``ledger_enabled`` always True                          -> the flag-off tests red;
  * break the ``voiced_in`` check (always 1)                     -> undelivered test red;
  * drop ``outcome IS NULL`` from the sweep's SELECT and UPDATE  -> double-sweep test red;
  * drop the ``judge_by`` fallback                               -> expiry test red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # every edge is stubbed; slim-dep modules only

import contextlib
import importlib.util
import inspect
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import db_compat
import zoe_core_client as core
import zoe_flue_client as flue
from db_pool import _Cursor, _ExecResult
from proactive import ledger
from proactive import selector as sel

SVC = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
MEMBER = "member-a"
GREET = "Hi Zoe, how are things?"
LOOP = "User is anxious about a job interview at the aquarium on Friday"


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


class _Mem:
    async def load_recent_for_prompt(self, user_id, **kw):
        return []


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    _migrate(engine, "0033_proactive_candidates.py")
    _migrate(engine, "0035_proactive_deliveries.py")
    db = _Sqlite(path)
    for ddl in (
        "CREATE TABLE open_loops (id INTEGER PRIMARY KEY, user_id TEXT, loop_text TEXT, "
        "follow_up_hint TEXT, emotional_weight INTEGER, created_at TEXT, follow_up_after TEXT, "
        "resolved BOOLEAN DEFAULT FALSE, resolved_at TEXT)",
        "CREATE TABLE events (id TEXT, user_id TEXT, title TEXT, start_date TEXT, start_time TEXT, "
        "deleted INTEGER DEFAULT 0)",
        "CREATE TABLE proactive_pending (id TEXT, trigger_type TEXT, item_id TEXT)",
    ):
        db.conn.execute(ddl)

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    import memory_service

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Mem())
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "1")
    monkeypatch.setenv("ZOE_PROACTIVE_LEDGER", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")
    for key in ("ZOE_SEAM_RECALL_INJECT", "ZOE_SEAM_OFFER_INJECT", "ZOE_BRIEF_ON_FIRST_TURN",
                "ZOE_SYNTHETIC_USER_ALLOWLIST", "ZOE_PROACTIVE_RAISE_GAP_S",
                "ZOE_PROACTIVE_RAISE_PER_DAY", "ZOE_LOOP_LIFECYCLE"):
        monkeypatch.delenv(key, raising=False)
    sel._reset_state()
    state = {"now": NOW, "db": db, "turns": {}}
    monkeypatch.setattr(sel, "_now", lambda: state["now"])

    async def fake_turns(_db, user_id, start, end, limit=1):
        return list(state["turns"].get(user_id, []))[:limit]

    monkeypatch.setattr(ledger, "_user_turns", fake_turns)
    yield state
    sel._reset_state()


def _seed(env, text=LOOP, user=MEMBER):
    created = (NOW - timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S")
    due = (NOW + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    env["db"].conn.execute(
        "INSERT INTO open_loops (user_id, loop_text, follow_up_hint, emotional_weight, created_at, "
        "follow_up_after) VALUES (?, ?, '', 5, ?, ?)", (user, text, created, due))


async def _raise_and_settle(env, *, reply="How did the aquarium interview go?", sid="s1"):
    _seed(env)
    await sel.select_for_user(MEMBER, now=NOW)
    raised = await sel.prepare(GREET, MEMBER, sid)
    assert raised is not None
    assert await sel.settle(raised, produced=True, reply=reply)
    return raised


def _ledger(env):
    return env["db"].rows("SELECT kind, shape, delivered_by, session_id, voiced, outcome "
                          "FROM proactive_deliveries ORDER BY created_at")


def _candidates(env):
    return env["db"].rows("SELECT user_id, kind, source_ref, text, salience, expires_at, "
                          "cooldown_until, surfaced_count, last_surfaced_session, "
                          "last_surfaced_at FROM proactive_candidates ORDER BY source_ref")


# ── migration ─────────────────────────────────────────────────────────────────
def test_migration_0035_is_idempotent_and_chained():
    engine = sa.create_engine("sqlite://")
    mod = _migrate(engine, "0035_proactive_deliveries.py")
    assert (mod.revision, mod.down_revision) == ("0035", "0034")
    _migrate(engine, "0035_proactive_deliveries.py")  # already present: a no-op
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(proactive_deliveries)")}
        idx = {r[1] for r in conn.exec_driver_sql("PRAGMA index_list(proactive_deliveries)")}
    assert cols == {"id", "idem_key", "user_id", "candidate_id", "kind", "source_ref", "shape",
                    "delivered_by", "session_id", "cue_words", "voiced", "surfaced_at",
                    "expires_at", "outcome", "outcome_at", "created_at"}
    assert {"idx_proactive_deliveries_open", "idx_proactive_deliveries_user"} <= idx
    _migrate(engine, "0035_proactive_deliveries.py", "downgrade")
    with engine.connect() as conn:
        assert not conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name='proactive_deliveries'").fetchall()


def test_no_request_time_ddl():
    for rel in ("proactive/ledger.py", "proactive/selector.py"):
        src = (SVC / rel).read_text()
        assert "CREATE TABLE" not in src and "ALTER TABLE" not in src, rel


# ── writer ────────────────────────────────────────────────────────────────────
async def test_a_raise_that_voices_the_item_is_recorded_pending(env):
    await _raise_and_settle(env)
    (row,) = _ledger(env)
    assert row == ("open_loop", "greeting", "turn", "s1", 1, None)  # surfaced, awaiting the sweep


async def test_a_raise_the_reply_never_voiced_is_undelivered_at_once(env, caplog):
    """The #1821 failure, finally visible: injected + settled, reply never mentioned it."""
    import logging

    with caplog.at_level(logging.INFO, logger="proactive.ledger"):
        await _raise_and_settle(env, reply="Good, thanks! How are you?")
    (row,) = _ledger(env)
    assert row[4:] == (0, "undelivered")
    assert "outcome=undelivered" in caplog.text
    assert "interview" not in caplog.text and "How are you" not in caplog.text  # no text in logs


async def test_no_reply_or_no_anchors_is_unverifiable_never_undelivered(env):
    await _raise_and_settle(env, reply=None)
    assert _ledger(env)[0][4:] == (None, None)
    assert ledger.voiced_in("anything at all", "") is None
    assert ledger.voiced_in(None, "aquarium") is None
    assert ledger.voiced_in("About the Aquarium!", "aquarium") == 1  # case + punctuation
    assert ledger.voiced_in("about the aquariums", "aquarium") == 1  # plural stem


async def test_duplicate_idempotency_key_is_one_row(env):
    raised = await _raise_and_settle(env)
    assert await sel.settle(raised, produced=True, reply="How did the aquarium go?")  # retried
    assert len(_ledger(env)) == 1
    again = await ledger.record(
        env["db"], user_id=MEMBER, candidate_id=raised.candidate_id, kind="open_loop",
        source_ref="open_loops:1", shape="greeting", delivered_by="turn", session_id="s1",
        cue_words="aquarium", now=NOW, reply="aquarium")
    assert again is False and len(_ledger(env)) == 1
    other_session = await ledger.record(
        env["db"], user_id=MEMBER, candidate_id=None, kind="open_loop", source_ref="open_loops:1",
        shape="greeting", delivered_by="turn", session_id="s2", cue_words="aquarium", now=NOW,
        reply="aquarium")
    assert other_session is True and len(_ledger(env)) == 2  # a different conversation is a different delivery


async def test_a_settle_without_text_writes_nothing(env):
    _seed(env)
    await sel.select_for_user(MEMBER, now=NOW)
    raised = await sel.prepare(GREET, MEMBER, "s1")
    assert await sel.settle(raised, produced=False, reply="") is False
    assert _ledger(env) == []


async def test_brief_items_are_recorded_with_shape_brief_and_are_idempotent(env):
    items = [("open_loop", "open_loops:7", "The aquarium interview is on Friday")]
    assert await sel.mark_brief_surfaced(MEMBER, "s9", items, reply="Your aquarium interview is Friday")
    assert await sel.mark_brief_surfaced(MEMBER, "s9", items, reply="Your aquarium interview is Friday")
    (row,) = _ledger(env)
    assert row == ("open_loop", "brief", "brief", "s9", 1, None)


# ── sweep ─────────────────────────────────────────────────────────────────────
async def _sweep_at(env, minutes):
    return await ledger.sweep(now=NOW + timedelta(minutes=minutes))


@pytest.mark.parametrize("turns, outcome", [
    (["The interview went well, they were lovely"], "accepted"),       # engages the item
    (["I had a long day at work today"], "ignored"),                  # a turn, a different topic
    (["turn on the lights"], "ignored"),                               # a deterministic command
    ([], "ignored"),                                                   # nobody said anything
])
async def test_question_outcome_from_the_members_next_turn(env, turns, outcome):
    await _raise_and_settle(env)
    env["turns"][MEMBER] = turns
    assert await _sweep_at(env, 1) == 0  # the window is still open: nothing is judged early
    assert _ledger(env)[0][5] is None
    assert await _sweep_at(env, 11) == 1
    assert _ledger(env)[0][5] == outcome


async def test_an_unverified_item_that_was_not_taken_up_is_unknown_not_ignored(env):
    await _raise_and_settle(env, reply=None)  # voiced NULL
    env["turns"][MEMBER] = ["I had a long day at work today"]
    await _sweep_at(env, 11)
    assert _ledger(env)[0][5] == "unknown"


async def test_an_event_is_accepted_when_voiced_without_waiting_for_an_answer(env):
    await ledger.record(env["db"], user_id=MEMBER, candidate_id=None, kind="event",
                        source_ref="events:e1", shape="cue", delivered_by="turn", session_id="s1",
                        cue_words="dentist", now=NOW, reply="You have the dentist at nine")
    await ledger.record(env["db"], user_id=MEMBER, candidate_id=None, kind="event",
                        source_ref="events:e2", shape="cue", delivered_by="turn", session_id="s1",
                        cue_words="", now=NOW, reply="whatever")
    assert await _sweep_at(env, 1) == 2
    assert [r[0] for r in env["db"].rows(
        "SELECT outcome FROM proactive_deliveries ORDER BY source_ref")] == ["accepted", "unknown"]


async def test_sweeping_twice_is_a_noop(env):
    await _raise_and_settle(env)
    env["turns"][MEMBER] = ["the interview went fine"]
    assert await _sweep_at(env, 11) == 1
    before = _ledger(env)
    stamp = env["db"].rows("SELECT outcome_at FROM proactive_deliveries")
    assert await _sweep_at(env, 30) == 0
    assert _ledger(env) == before
    assert env["db"].rows("SELECT outcome_at FROM proactive_deliveries") == stamp


async def test_expiry_closes_a_row_the_sweep_could_not_judge(env, monkeypatch):
    await _raise_and_settle(env)

    async def broken(*a, **k):
        raise RuntimeError("chat read down")

    monkeypatch.setattr(ledger, "_user_turns", broken)
    assert await _sweep_at(env, 11) == 0          # unreadable: left open, retried next tick
    assert _ledger(env)[0][5] is None
    assert await ledger.sweep(now=NOW + ledger.JUDGE_BY + timedelta(minutes=1)) == 1
    assert _ledger(env)[0][5] == "unknown"        # nothing strands


async def test_an_unreadable_turn_leaves_the_row_open(env, monkeypatch):
    await _raise_and_settle(env)
    env["turns"][MEMBER] = ["hello there"]

    def boom(text):
        raise RuntimeError("intent router down")

    monkeypatch.setattr(ledger, "_is_command", boom)
    assert await _sweep_at(env, 11) == 0
    assert _ledger(env)[0][5] is None


# ── flag off = the pre-ledger behaviour, byte for byte ────────────────────────
async def _scenario(env):
    _seed(env)
    await sel.select_for_user(MEMBER, now=NOW)
    first = await sel.prepare(GREET, MEMBER, "s1")
    assert await sel.settle(first, produced=True, reply="How did the aquarium interview go?")
    env["now"] = NOW + timedelta(seconds=sel.raise_gap_s() + 1)
    again = await sel.prepare(GREET, MEMBER, "s2")
    return first.block, (again.block if again else None), _candidates(env)


async def test_flag_off_selector_behaviour_is_identical_and_writes_no_ledger(env, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_LEDGER", "0")
    off = await _scenario(env)
    assert _ledger(env) == []
    # Same scenario, flag ON, fresh DB state: the ledger rows are the ONLY difference.
    env["db"].conn.execute("DELETE FROM proactive_candidates")
    env["db"].conn.execute("DELETE FROM open_loops")
    sel._reset_state()
    env["now"] = NOW
    monkeypatch.setenv("ZOE_PROACTIVE_LEDGER", "1")
    on = await _scenario(env)
    assert _ledger(env) != []
    assert off == on


async def test_flag_off_does_no_io_at_all(env, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_LEDGER", "off")
    await _raise_and_settle(env)
    calls = env["db"].calls
    assert await ledger.sweep(now=NOW) == 0
    assert await ledger.record(env["db"], user_id=MEMBER, candidate_id=None, kind="event",
                               source_ref="x", shape="cue", delivered_by="turn", session_id="s",
                               cue_words="a", now=NOW, reply="a") is False
    assert await ledger.record_for_candidate(
        env["db"], candidate_id="x", user_id=MEMBER, session_id="s", shape="cue", now=NOW,
        reply="a") is False
    assert env["db"].calls == calls
    assert ledger.reply_tap().kwargs() == {}


def test_engine_slow_loop_runs_the_sweep():
    from proactive import engine

    assert "proactive.ledger" in inspect.getsource(engine._slow_loop)


# ── both lanes pass the reply only when the ledger is on ──────────────────────
def _flue_turn(monkeypatch, reply):
    async def fake_turn(message, session_id, user_id="", **kwargs):
        yield "__TOOL__:x"
        for part in reply:
            yield part

    monkeypatch.setattr(flue, "_run_flue_brain_streaming_turn", fake_turn)


async def test_flue_lane_ledger_on_voiced_and_off_no_reply_kwarg(env, monkeypatch):
    _seed(env)
    await sel.select_for_user(MEMBER, now=NOW)
    _flue_turn(monkeypatch, ["How did the aquarium ", "interview go?"])  # voiced across deltas
    async for _ in flue.run_flue_brain_streaming(GREET, "s1", MEMBER):
        pass
    assert _ledger(env)[0][4:] == (1, None)

    seen, calls = {}, []

    async def spy(raised, *, produced, **kw):
        calls.append(produced)
        seen.update(kw)
        return True

    monkeypatch.setattr(sel, "settle", spy)
    monkeypatch.setenv("ZOE_PROACTIVE_LEDGER", "0")
    await sel.select_for_user(MEMBER, now=NOW)
    sel._reset_state()
    env["now"] = NOW + timedelta(days=4)
    await sel.select_for_user(MEMBER, now=env["now"])
    async for _ in flue.run_flue_brain_streaming(GREET, "s2", MEMBER):
        pass
    assert calls == [True] and seen == {}  # flag off: the settle call is exactly the pre-ledger call


class _Worker:
    def __init__(self, reply):
        self.reply = reply

    async def stream(self, compose, *, timeout_s):
        await compose()
        for part in self.reply:
            yield part


async def test_core_lane_passes_the_reply_to_the_ledger(env, monkeypatch):
    _seed(env)
    await sel.select_for_user(MEMBER, now=NOW)

    async def fake_worker_for(*a, **k):
        return _Worker(["Good, thanks! How are you?"])

    async def no_packet(*a, **k):
        return ""

    monkeypatch.setattr(core, "_worker_for", fake_worker_for)
    monkeypatch.setattr(core, "_memory_packet_block", no_packet)
    async for _ in core.run_zoe_core_streaming(GREET, "s1", MEMBER):
        pass
    assert _ledger(env)[0][4:] == (0, "undelivered")  # injected, settled, never voiced
