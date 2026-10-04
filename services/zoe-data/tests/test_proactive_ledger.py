"""Delivery ledger (``proactive/ledger.py``, ``ZOE_PROACTIVE_LEDGER``) — pull-not-push inbox PR 1.

Fixtures only — no household text, no real ids. The DB edge is a real SQLite file built by
migrations 0033 + 0035 behind db_pool's own cursor types, so the idempotent INSERT, the
outcome UPDATE and the selector's own SQL run for real; the two Postgres-only chat reads
(``ledger._reply_after`` / ``ledger._user_turns``) are replaced by time-honouring fakes, like
every other ``ci_safe`` test of a Postgres edge.

Negative controls (each was run red before commit):
  * flag off  -> no row, no sweep DB access, and the candidate table + raise blocks are
    byte-identical to a flag-on run;
  * drop ``idem_key``'s UNIQUE from migration 0035               -> duplicate-key test red;
  * make ``ledger_enabled`` always True                          -> the flag-off tests red;
  * make ``voiced_in`` always 1                                  -> undelivered test red;
  * start the next-turn window BEFORE the reply                  -> trigger-turn test red;
  * drop ``outcome IS NULL`` from the sweep's SELECT and UPDATE  -> double-sweep test red;
  * drop the ``judge_by`` fallback                               -> expiry test red;
  * ignore the response window                                   -> window test red.
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
from db_pool import _Cursor, _ExecResult
from proactive import ledger
from proactive import selector as sel

SVC = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
MEMBER = "member-a"
GREET = "Hi Zoe, how are things?"
LOOP = "User is anxious about a job interview at the aquarium on Friday"  # anchors: interview, job


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
    state = {"now": NOW, "db": db, "turns": {}, "replies": {}}
    monkeypatch.setattr(sel, "_now", lambda: state["now"])

    async def fake_reply_after(_db, session_id, since):
        # the first assistant row of the session at/after ``since`` (chat_messages)
        due = [(text, at) for text, at in state["replies"].get(session_id, [])
               if ledger._iso(at) >= since]
        return min(due, key=lambda r: r[1]) if due else None

    async def fake_turns(_db, user_id, start, end, limit=1):
        # the member's user rows in (start, end], oldest first
        rows = sorted((at, text) for text, at in state["turns"].get(user_id, [])
                      if start < ledger._iso(at) <= end)
        return [text for _at, text in rows][:limit]

    monkeypatch.setattr(ledger, "_reply_after", fake_reply_after)
    monkeypatch.setattr(ledger, "_user_turns", fake_turns)
    yield state
    sel._reset_state()


def _seed(env, text=LOOP, user=MEMBER):
    created = (NOW - timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S")
    due = (NOW + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    env["db"].conn.execute(
        "INSERT INTO open_loops (user_id, loop_text, follow_up_hint, emotional_weight, created_at, "
        "follow_up_after) VALUES (?, ?, '', 5, ?, ?)", (user, text, created, due))


async def _raise_and_settle(env, *, sid="s1"):
    _seed(env)
    await sel.select_for_user(MEMBER, now=NOW)
    raised = await sel.prepare(GREET, MEMBER, sid)
    assert raised is not None
    assert await sel.settle(raised, produced=True)
    return raised


def _persist_reply(env, text="How did the interview go?", *, sid="s1", after_s=2):
    """Chat persisted the reply the person heard, ``after_s`` after the settle."""
    env["replies"].setdefault(sid, []).append((text, NOW + timedelta(seconds=after_s)))


def _says(env, text, *, after_s):
    env["turns"].setdefault(MEMBER, []).append((text, NOW + timedelta(seconds=after_s)))


async def _sweep_at(env, minutes):
    return await ledger.sweep(now=NOW + timedelta(minutes=minutes))


def _ledger(env):
    """(kind, shape, delivered_by, session, voiced, outcome)"""
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
async def test_a_settled_raise_is_recorded_open(env):
    await _raise_and_settle(env)
    (row,) = _ledger(env)
    assert row == ("open_loop", "greeting", "turn", "s1", None, None)  # surfaced, awaiting the sweep


async def test_duplicate_idempotency_key_is_one_row(env):
    raised = await _raise_and_settle(env)
    assert await sel.settle(raised, produced=True)  # a retried settle
    assert len(_ledger(env)) == 1
    again = await ledger.record(
        env["db"], user_id=MEMBER, candidate_id=raised.candidate_id, kind="open_loop",
        source_ref="open_loops:1", shape="greeting", delivered_by="turn", session_id="s1",
        cue_words="interview", now=NOW)
    assert again is False and len(_ledger(env)) == 1
    other_session = await ledger.record(
        env["db"], user_id=MEMBER, candidate_id=None, kind="open_loop", source_ref="open_loops:1",
        shape="greeting", delivered_by="turn", session_id="s2", cue_words="interview", now=NOW)
    assert other_session is True and len(_ledger(env)) == 2  # another conversation = another delivery


async def test_a_settle_without_text_writes_nothing(env):
    _seed(env)
    await sel.select_for_user(MEMBER, now=NOW)
    raised = await sel.prepare(GREET, MEMBER, "s1")
    assert await sel.settle(raised, produced=False) is False
    assert _ledger(env) == []


async def test_brief_items_are_recorded_with_shape_brief_and_are_idempotent(env):
    items = [("open_loop", "open_loops:7", "The aquarium interview is on Friday")]
    assert await sel.mark_brief_surfaced(MEMBER, "s9", items)
    assert await sel.mark_brief_surfaced(MEMBER, "s9", items)
    (row,) = _ledger(env)
    assert row == ("open_loop", "brief", "brief", "s9", None, None)


def test_voiced_in():
    assert ledger.voiced_in("anything at all", "") is None      # no anchors: unverifiable
    assert ledger.voiced_in(None, "interview") is None
    assert ledger.voiced_in("About the Interview!", "interview") == 1   # case + punctuation
    assert ledger.voiced_in("about the interviews", "interview") == 1   # plural stem
    assert ledger.voiced_in("Good, thanks! How are you?", "interview job") == 0


# ── sweep: voiced / undelivered ───────────────────────────────────────────────
async def test_a_reply_that_never_voiced_the_item_is_undelivered(env, caplog):
    """The #1821 failure, finally visible: injected + settled, the reply never mentioned it."""
    import logging

    await _raise_and_settle(env)
    _persist_reply(env, "Good, thanks! How are you?")
    with caplog.at_level(logging.INFO, logger="proactive.ledger"):
        assert await _sweep_at(env, 1) == 1
    assert _ledger(env)[0][4:] == (0, "undelivered")  # closed at once, no 10-minute wait
    assert "outcome=undelivered" in caplog.text
    assert "interview" not in caplog.text and "How are you" not in caplog.text  # no text in logs


async def test_no_persisted_reply_yet_leaves_the_row_open(env):
    await _raise_and_settle(env)
    assert await _sweep_at(env, 30) == 0          # nothing persisted: wait, never guess
    assert _ledger(env)[0][5] is None


async def test_a_reply_persisted_before_the_settle_is_not_this_deliverys(env):
    """The previous exchange's reply (older than the settle slack) must not be mistaken."""
    await _raise_and_settle(env)
    _persist_reply(env, "Good, thanks! How are you?", after_s=-60)
    assert await _sweep_at(env, 30) == 0


async def test_unanchored_item_is_unknown_never_undelivered(env):
    await ledger.record(env["db"], user_id=MEMBER, candidate_id=None, kind="open_loop",
                        source_ref="open_loops:9", shape="greeting", delivered_by="turn",
                        session_id="s1", cue_words="", now=NOW)
    _persist_reply(env, "Good, thanks! How are you?")
    _says(env, "I had a long day", after_s=60)
    await _sweep_at(env, 15)
    assert _ledger(env)[0][4:] == (None, "unknown")


# ── sweep: the member's next turn ─────────────────────────────────────────────
@pytest.mark.parametrize("turn, outcome", [
    ("The interview went well, they were lovely", "accepted"),   # engages the item
    ("I had a long day at work today", "ignored"),               # a turn, a different topic
    ("turn on the lights", "ignored"),                           # a deterministic command
    (None, "ignored"),                                           # nobody said anything
])
async def test_question_outcome_from_the_members_next_turn(env, turn, outcome):
    await _raise_and_settle(env)
    _persist_reply(env)
    if turn:
        _says(env, turn, after_s=60)
    assert await _sweep_at(env, 1) == 0   # the window is still open: nothing is judged early
    assert _ledger(env)[0][5] is None
    assert await _sweep_at(env, 11) == 1
    assert _ledger(env)[0][4:] == (1, outcome)


async def test_the_turn_that_triggered_the_delivery_is_never_its_answer(env):
    """The non-streaming voice lane saves the user row AFTER the settle, together with the
    reply. The window starts at the reply, so that row (earlier than it) cannot be 'next'."""
    await _raise_and_settle(env)
    _says(env, "Hey Zoe, the interview is on my mind", after_s=1)   # saved with the reply, before it
    _persist_reply(env, after_s=2)
    _says(env, "I had a long day at work today", after_s=90)
    await _sweep_at(env, 11)
    assert _ledger(env)[0][5] == "ignored"        # judged on the 90 s turn, not the trigger


async def test_a_turn_after_the_window_does_not_count(env):
    await _raise_and_settle(env)
    _persist_reply(env)
    _says(env, "The interview went well", after_s=ledger.RESPONSE_WINDOW_S + 60)
    await _sweep_at(env, 11)
    assert _ledger(env)[0][5] == "ignored"


async def test_an_event_is_accepted_when_voiced_without_waiting_for_an_answer(env):
    for ref in ("events:e1", "events:e2"):
        await ledger.record(env["db"], user_id=MEMBER, candidate_id=None, kind="event",
                            source_ref=ref, shape="cue", delivered_by="turn", session_id="s1",
                            cue_words="dentist" if ref == "events:e1" else "", now=NOW)
    _persist_reply(env, "You have the dentist at nine")
    assert await _sweep_at(env, 1) == 2
    assert [r[0] for r in env["db"].rows(
        "SELECT outcome FROM proactive_deliveries ORDER BY source_ref")] == ["accepted", "unknown"]


async def test_sweeping_twice_is_a_noop(env):
    await _raise_and_settle(env)
    _persist_reply(env)
    _says(env, "the interview went fine", after_s=60)
    assert await _sweep_at(env, 11) == 1
    before = _ledger(env)
    stamp = env["db"].rows("SELECT outcome_at FROM proactive_deliveries")
    assert await _sweep_at(env, 30) == 0
    assert _ledger(env) == before
    assert env["db"].rows("SELECT outcome_at FROM proactive_deliveries") == stamp


async def test_expiry_closes_a_row_the_sweep_could_not_judge(env, monkeypatch):
    await _raise_and_settle(env)
    _persist_reply(env)

    async def broken(*a, **k):
        raise RuntimeError("chat read down")

    monkeypatch.setattr(ledger, "_user_turns", broken)
    assert await _sweep_at(env, 11) == 0          # unreadable: left open, retried next tick
    assert _ledger(env)[0][5] is None
    assert await ledger.sweep(now=NOW + ledger.JUDGE_BY + timedelta(minutes=1)) == 1
    assert _ledger(env)[0][5] == "unknown"        # nothing strands


async def test_an_unreadable_turn_leaves_the_row_open(env, monkeypatch):
    await _raise_and_settle(env)
    _persist_reply(env)
    _says(env, "hello there", after_s=60)

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
    assert await sel.settle(first, produced=True)
    env["now"] = NOW + timedelta(seconds=sel.raise_gap_s() + 1)
    again = await sel.prepare(GREET, MEMBER, "s2")
    return first.block, (again.block if again else None), _candidates(env)


async def test_flag_off_selector_behaviour_is_identical_and_writes_no_ledger(env, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_LEDGER", "0")
    off = await _scenario(env)
    assert _ledger(env) == []
    # Same scenario, flag ON, fresh state: the ledger rows are the ONLY difference.
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
                               cue_words="a", now=NOW) is False
    assert await ledger.record_for_candidate(
        env["db"], candidate_id="x", user_id=MEMBER, session_id="s", shape="cue", now=NOW) is False
    assert env["db"].calls == calls


def test_engine_slow_loop_runs_the_sweep():
    from proactive import engine

    assert "proactive.ledger" in inspect.getsource(engine._slow_loop)


@pytest.mark.parametrize("rel", ["zoe_core_client.py", "zoe_flue_client.py", "brief_first_turn.py",
                                 "routers/voice_tts.py"])
def test_the_ledger_stays_out_of_the_voice_path_files(rel):
    """The brain lanes and voice_tts are VOICE_PATH_PATTERNS: an edit there needs a Jetson
    replay-gate run bound to the head. The ledger reads what chat persisted instead."""
    src = (SVC / rel).read_text()
    assert "proactive.ledger" not in src and "proactive import ledger" not in src
    assert "ZOE_PROACTIVE_LEDGER" not in src
