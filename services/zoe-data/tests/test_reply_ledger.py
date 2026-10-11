"""The persisted "what did each reply stand on" ledger (``reply_ledger``, migration 0044; BM5 follow-up, ``ZOE_PROVENANCE_PERSIST``).

A zoe-data restart used to empty the in-process ledger, so "why did you say that?" answered "I don't have a record". Now one id-only
row per reply is written (background task) keyed by (user, session, reply id) and read back for the SAME conversation in the restart
case. Walls, each with a break-the-fix control (``test_control_*`` / the flag-off arms):

  restart        the persisted ledger answers after the in-process one is cleared; with the feature off it does not
  per-session    a voice session never explains a chat reply (in process AND on disk); a turn with no session id has no key
  off the record an off-the-record turn and a distress turn write NOTHING; a normal turn beside them does (the control)
  ids only       the row holds ids, tool names and labels - never a reply, a quote or the user's words
  forgotten      a row forgotten since the reply is not quoted from the table
  mid-session    an empty in-process ledger mid-conversation (a lane that recorded nothing) is NOT filled from disk
  retention      rows older than ``RETENTION_S`` go; a row older than ``READ_WINDOW_S`` is not used

The DB edge is a real SQLite file built by migration 0044 behind ``db_compat``; the memory store and the packet builder are the same
fakes ``test_provenance_answers`` uses. Synthetic data only.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import asyncio
import contextlib
import importlib.util
import json
import logging
import sqlite3
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import db_compat
import memory_provenance as mp
import provenance_answers as pa
import reply_ledger as rl
import user_erase_gate as erase_gate
from db_pool import _Cursor, _ExecResult
# the fixtures and helpers of the BM5 suite (autouse clock/flag reset, the fake memory store, the seed helpers)
from test_provenance_answers import (  # noqa: F401
    ASK_SISTER, NOW, OTHER, REPLY_SISTER, ROW_SISTER, SAY_SISTER, UID, _clean, put, run, svc,
)

SVC = Path(__file__).resolve().parents[1]
WHY = "why did you say that"


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        async def _run():
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()

    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


def migrate(path, *names):
    engine = sa.create_engine(f"sqlite:///{path}")
    for fname in names:
        spec = importlib.util.spec_from_file_location("mig_" + fname[:4], SVC / "alembic/versions" / fname)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()


@pytest.fixture
def ledger_db(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    migrate(path, "0044_reply_sources_commitments.py")
    db = _Sqlite(path)

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.delenv(rl.ENV, raising=False)
    monkeypatch.setenv("ZOE_COMMITMENTS", "off")           # this file is about the ledger; the promise tracker has its own
    erase_gate.reset()
    monkeypatch.setattr(rl, "_pending", set())          # module globals through monkeypatch: restored after the test, never leaked
    monkeypatch.setattr(rl, "_last_purge", 0.0)
    yield db


async def _turn(user, message, reply, *, session="s1", svc_rows=(), tools=("recall_memory",)):
    """One brain turn as the live path runs it (user turn numbered, packet served, reply committed by brain_dispatch's hook), then
    the background write is awaited."""
    import brain_dispatch

    mp.note_user_turn(user, message, session)
    mp.claim_turn(user, message)
    token = brain_dispatch._provenance_begin(user)
    if svc_rows:
        mp.note_served(user, list(svc_rows))
    brain_dispatch._provenance_commit(user, reply, message, session, token, tuple(tools))
    await rl.flush()


def sister_rows(svc):
    return [(r.id, r.text) for r in run(svc.list_by_status(user_id=UID, status="approved", limit=50))
            + run(svc.list_by_status(user_id=UID, status="pending", limit=50)) if "Marisol" in r.text]


def restart():
    """zoe-data restarted: every in-process ledger is gone; the database is not."""
    mp.reset()


def ask_why(svc, *, session="s1", user=UID, channel="chat"):
    mp.note_user_turn(user, WHY, session)
    return run(pa.explain(user, svc=svc, now=NOW, channel=channel, session_id=session))


# ── restart ──────────────────────────────────────────────────────────────────

def test_a_restart_still_answers_why_from_the_persisted_ledger(svc, ledger_db):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, svc_rows=sister_rows(svc)))
    assert len(ledger_db.rows("SELECT * FROM reply_sources")) == 1
    restart()
    out = ask_why(svc)
    assert out != pa.UNKNOWN_REPLY and "Marisol" in out and "Lisbon" in out        # the owner's own words, re-read from the row


def test_control_without_the_persisted_ledger_a_restart_answers_i_dont_have_a_record(svc, ledger_db, monkeypatch):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, svc_rows=sister_rows(svc)))
    restart()
    monkeypatch.setenv(rl.ENV, "0")
    assert ask_why(svc) == pa.UNKNOWN_REPLY


def test_control_nothing_is_written_with_the_flag_off_so_a_restart_has_nothing_to_read(svc, ledger_db, monkeypatch):
    monkeypatch.setenv(rl.ENV, "0")
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, svc_rows=sister_rows(svc)))
    assert ledger_db.rows("SELECT * FROM reply_sources") == []
    restart()
    monkeypatch.delenv(rl.ENV)
    assert ask_why(svc) == pa.UNKNOWN_REPLY


def test_a_direct_tier_reply_is_persisted_too(svc, ledger_db):
    async def go():
        mp.note_user_turn(UID, "what time is it", "s1")
        mp.note_direct_reply(UID, "tier0", "s1", domain="time")
        await rl.flush()
    asyncio.run(go())
    restart()
    assert "clock" in ask_why(svc)


def test_the_reply_is_not_delayed_by_the_write_and_a_dead_database_costs_only_the_record(svc, monkeypatch):
    """No ``ledger_db``: ``get_compat_db`` has no pool, the write fails soft, the in-process record still works."""
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    monkeypatch.setenv("ZOE_COMMITMENTS", "off")
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, svc_rows=sister_rows(svc)))
    assert "Marisol" in ask_why(svc)                       # the in-process ledger answered; the failed write cost nothing else


# ── per conversation ─────────────────────────────────────────────────────────

def test_a_voice_session_never_explains_a_chat_reply_after_a_restart(svc, ledger_db):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, session="chat-1", svc_rows=sister_rows(svc)))
    restart()
    assert ask_why(svc, session="voice-9", channel="voice") == pa.UNKNOWN_REPLY
    restart()
    assert "Marisol" in ask_why(svc, session="chat-1")                              # the chat itself still gets its own


def test_a_voice_session_never_explains_a_chat_reply_in_process_either(svc, ledger_db):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, session="chat-1", svc_rows=sister_rows(svc)))
    mp.note_user_turn(UID, WHY, "voice-9")
    assert mp.previous_reply(UID, now=NOW, session_id="voice-9") is None
    assert mp.previous_reply(UID, now=NOW, session_id="chat-1") is not None
    assert run(pa.explain(UID, svc=svc, now=NOW, session_id="voice-9")) == pa.UNKNOWN_REPLY


def test_two_conversations_each_get_their_own_reply_after_a_restart(svc, ledger_db):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    rows = sister_rows(svc)

    async def go():
        await _turn(UID, ASK_SISTER, REPLY_SISTER, session="chat-1", svc_rows=rows)
        mp.note_user_turn(UID, "what time is it", "voice-9")
        mp.note_direct_reply(UID, "tier0", "voice-9", domain="time")
        await rl.flush()
    asyncio.run(go())
    restart()
    assert "clock" in ask_why(svc, session="voice-9", channel="voice")
    restart()
    out = ask_why(svc, session="chat-1")
    assert "Marisol" in out and "clock" not in out


def test_a_turn_with_no_session_id_writes_and_reads_nothing(svc, ledger_db):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, session="", svc_rows=sister_rows(svc)))
    assert ledger_db.rows("SELECT * FROM reply_sources") == []
    assert run(rl.read_latest(UID, "", now=NOW)) is None


def test_another_member_cannot_read_a_row_they_do_not_own(svc, ledger_db):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, session="shared", svc_rows=sister_rows(svc)))
    restart()
    assert run(rl.read_latest(OTHER, "shared", now=NOW)) is None


def test_an_empty_in_process_ledger_mid_conversation_is_not_filled_from_disk(svc, ledger_db):
    """A lane that records nothing is "I can't tell" - never the previous reply's sources. Only the conversation's FIRST turn since
    the process started (the restart signature) may read the table."""
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, svc_rows=sister_rows(svc)))
    restart()
    mp.note_user_turn(UID, "and what about my dad", "s1")              # a turn the new process saw, whose lane recorded nothing
    assert ask_why(svc) == pa.UNKNOWN_REPLY


# ── off the record, distress, guests ─────────────────────────────────────────

def test_an_off_the_record_turn_writes_nothing_and_a_normal_turn_beside_it_does(svc, ledger_db):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, "Off the record: my sister Marisol is flying in from Lisbon on Thursday", REPLY_SISTER, session="s1",
                      svc_rows=sister_rows(svc)))
    assert ledger_db.rows("SELECT * FROM reply_sources") == []                      # the marked turn's reply: nothing
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, session="s2", svc_rows=sister_rows(svc)))
    assert len(ledger_db.rows("SELECT * FROM reply_sources")) == 1                  # the control: a normal turn lands


def test_a_bare_off_the_record_cue_arms_the_next_turn_and_that_reply_is_not_written(svc, ledger_db):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    mp.note_user_turn(UID, "off the record", "s1")
    assert mp.claim_turn(UID, "off the record") is False
    asyncio.run(_turn(UID, "my sister Marisol is flying in from Lisbon on Thursday", REPLY_SISTER, svc_rows=sister_rows(svc)))
    assert ledger_db.rows("SELECT * FROM reply_sources") == []


def test_a_distress_turn_writes_nothing(svc, ledger_db):
    asyncio.run(_turn(UID, "I want to die", "I'm here with you. Please call the number I gave you.", session="s1"))
    assert ledger_db.rows("SELECT * FROM reply_sources") == []


def test_control_removing_the_off_record_wall_writes_the_marked_reply(svc, ledger_db, monkeypatch):
    """Break-the-fix: with the wall gone (``skip_reason`` blind to off-record) the marked turn's reply IS written - the walls above
    are what stops it."""
    monkeypatch.setattr(mp, "is_off_record", lambda *a, **k: False)
    monkeypatch.setattr(mp, "reply_is_off_record", lambda *a, **k: False)
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, "Off the record: my sister Marisol is flying in from Lisbon on Thursday", REPLY_SISTER,
                      svc_rows=sister_rows(svc)))
    assert len(ledger_db.rows("SELECT * FROM reply_sources")) == 1


@pytest.mark.parametrize("guest", ["guest", "voice-guest", "anonymous", "guest-ab12"])
def test_a_guest_keeps_nothing(svc, ledger_db, guest):
    asyncio.run(_turn(guest, "what time is it", "It is five.", session="s1"))
    assert ledger_db.rows("SELECT * FROM reply_sources") == []


# ── what is stored ───────────────────────────────────────────────────────────

def test_the_row_holds_ids_tool_names_and_labels_and_none_of_the_words(svc, ledger_db):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, svc_rows=sister_rows(svc), tools=("recall_memory", "add_reminder")))
    (row,) = ledger_db.rows("SELECT * FROM reply_sources")
    blob = json.dumps([str(v) for v in tuple(row)])
    for word in ("Marisol", "Lisbon", "Thursday", "sister", "arriving", "pick her up"):
        assert word not in blob
    assert json.loads(row["tools"]) == ["recall_memory", "add_reminder"]
    assert json.loads(row["sources"])[0][0] == "row" and row["session_id"] == "s1" and row["user_id"] == UID
    assert row["served"] == 1 and len(row["reply_id"]) == 16


def test_tool_names_come_from_the_sentinels_and_never_carry_arguments_or_results():
    got = rl.tool_names([
        '__TOOL__:{"phase": "start", "id": "a", "name": "add_reminder"}',
        '__TOOL__:{"phase": "args", "id": "a", "name": "add_reminder", "args": {"title": "secret dentist"}}',
        '__TOOL__:{"phase": "result", "id": "a", "result": "Reminder set for secret dentist"}',
        '__TOOL__:not json', "plain text", '__TOOL__:{"phase": "start", "id": "b", "name": "recall_memory"}'])
    assert got == ("add_reminder", "recall_memory") and "secret" not in repr(got)


def test_a_row_forgotten_since_the_reply_is_not_quoted_from_the_table(svc, ledger_db):
    ref = put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, svc_rows=sister_rows(svc)))
    run(svc.review(ref.id, decision="reject", actor=UID, note="forgotten"))
    restart()
    out = ask_why(svc)
    assert out == pa.FORGOTTEN_REPLY and "Marisol" not in out


# ── retention / window ───────────────────────────────────────────────────────

def test_rows_older_than_the_retention_are_purged_on_the_next_write(ledger_db):
    def rec(ts, rid):
        return mp.ReplyRecord(seq=1, ts=ts, kind="direct", tier="tier0", session_id="s1", reply_id=rid)

    async def go():
        assert await rl.write(UID, rec(NOW - 4 * 86400, "old0000000000000"))
        assert await rl.write(UID, rec(NOW, "new0000000000000"))
    asyncio.run(go())
    assert [r["reply_id"] for r in ledger_db.rows("SELECT reply_id FROM reply_sources")] == ["new0000000000000"]


def test_a_persisted_reply_older_than_the_read_window_is_not_used(svc, ledger_db):
    def rec(ts):
        return mp.ReplyRecord(seq=1, ts=ts, kind="direct", tier="tier0", domain="time", session_id="s1", reply_id="r000000000000001")
    asyncio.run(rl.write(UID, rec(NOW - rl.READ_WINDOW_S - 60)))
    assert run(rl.read_latest(UID, "s1", now=NOW)) is None
    asyncio.run(rl.write(UID, mp.ReplyRecord(seq=1, ts=NOW - 60, kind="direct", tier="tier0", domain="time", session_id="s1",
                                             reply_id="r000000000000002")))
    got = run(rl.read_latest(UID, "s1", now=NOW))
    assert got is not None and got.reply_id == "r000000000000002" and got.session_id == "s1"


def test_forgetting_the_user_drops_their_persisted_rows(svc, ledger_db):
    asyncio.run(_turn(UID, "what time is it", "It is five.", session="s1"))
    asyncio.run(_turn(OTHER, "what time is it", "It is five.", session="s1"))
    assert len(ledger_db.rows("SELECT * FROM reply_sources")) == 2
    assert run(rl.forget_user(UID)) == 1
    assert [r["user_id"] for r in ledger_db.rows("SELECT user_id FROM reply_sources")] == [OTHER]


# ── controls: each wall's test goes red when the wall is bypassed IN THE TEST (the production code is never edited) ──────────────

def test_control_keyed_by_user_alone_a_voice_session_is_shown_the_chat_reply(svc, ledger_db, monkeypatch):
    """With the read keyed by user only (the per-conversation key dropped), the voice session IS shown the chat reply - the exact
    failure ``test_a_voice_session_never_explains_a_chat_reply_after_a_restart`` guards."""
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, session="chat-1", svc_rows=sister_rows(svc)))
    restart()
    real = rl.read_latest

    async def by_user(user_id, session_id, *, now=None):
        return await real(user_id, "chat-1", now=now)
    monkeypatch.setattr(rl, "read_latest", by_user)
    assert "Marisol" in ask_why(svc, session="voice-9", channel="voice")


def test_control_without_the_in_process_session_guard_a_voice_session_is_shown_the_chat_reply(svc, ledger_db, monkeypatch):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, session="chat-1", svc_rows=sister_rows(svc)))
    real = mp.previous_reply
    monkeypatch.setattr(mp, "previous_reply", lambda user_id, *, now=None, session_id="": real(user_id, now=now))
    assert "Marisol" in ask_why(svc, session="voice-9", channel="voice")


def test_control_without_the_restart_signature_a_silent_lane_is_filled_from_disk(svc, ledger_db, monkeypatch):
    put(svc, ROW_SISTER, excerpt=SAY_SISTER)
    asyncio.run(_turn(UID, ASK_SISTER, REPLY_SISTER, svc_rows=sister_rows(svc)))
    restart()
    mp.note_user_turn(UID, "and what about my dad", "s1")
    monkeypatch.setattr(mp, "first_turn_in_session", lambda *a, **k: True)
    assert "Marisol" in ask_why(svc)


def test_the_migration_is_the_next_one_and_single_headed():
    import re
    versions = SVC / "alembic" / "versions"
    revs = {}
    for p in versions.glob("[0-9][0-9][0-9][0-9]_*.py"):
        text = p.read_text()
        revs[re.search(r'^revision = "(\w+)"', text, re.M).group(1)] = re.search(r'^down_revision = "?(\w+)"?', text, re.M).group(1)
    heads = set(revs) - set(revs.values())
    assert heads == {max(revs)} and revs["0044"] == "0043"


# ── review round 1 (Greptile on #1981): each test fails when its fix is reverted ─────────────────

def _rec(rid, ts=NOW, session="s1"):
    return mp.ReplyRecord(seq=1, ts=ts, kind="direct", tier="tier0", domain="time", session_id=session, reply_id=rid)


def test_a_skipped_off_record_reply_is_not_followed_by_the_reply_before_it_after_a_restart(svc, ledger_db):
    """A clock reply is persisted; the NEXT reply is off the record and skipped; zoe-data restarts. 'Why did you say that?' must not be
    answered with the clock reply (it is not the reply being asked about): it is 'I can't tell'."""
    async def clock():
        mp.note_user_turn(UID, "what time is it", "s1")
        mp.note_direct_reply(UID, "tier0", "s1", domain="time")
        await rl.flush()
    asyncio.run(clock())
    assert len(ledger_db.rows("SELECT * FROM reply_sources")) == 1
    asyncio.run(_turn(UID, "Off the record: my sister Marisol is flying in from Lisbon on Thursday", REPLY_SISTER, session="s1"))
    assert ledger_db.rows("SELECT * FROM reply_sources") == []                      # the skipped reply left nothing, and took the stale row
    restart()
    assert ask_why(svc) == pa.UNKNOWN_REPLY


def test_a_skipped_reply_only_drops_its_own_conversations_rows(svc, ledger_db):
    async def go():
        for sid in ("s1", "s2"):
            mp.note_user_turn(UID, "what time is it", sid)
            mp.note_direct_reply(UID, "tier0", sid, domain="time")
        await rl.flush()
    asyncio.run(go())
    asyncio.run(_turn(UID, "Off the record: my sister Marisol is flying in", REPLY_SISTER, session="s1"))
    assert [r["session_id"] for r in ledger_db.rows("SELECT session_id FROM reply_sources")] == ["s2"]


def test_the_reply_time_is_stored_as_double_precision_not_real(monkeypatch):
    """PostgreSQL REAL is 4 bytes: an epoch near 1.8e9 rounds to a multiple of 128 s, so replies a minute apart tie in ORDER BY ts DESC.
    (SQLite stores any numeric type name as an 8-byte float, so only the DDL text can show it here.)"""
    import re
    import alembic.op as alembic_op
    statements: list = []
    monkeypatch.setattr(alembic_op, "execute", lambda sql: statements.append(str(sql)))
    spec = importlib.util.spec_from_file_location("mig_0044_ddl", SVC / "alembic/versions/0044_reply_sources_commitments.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.upgrade()
    (ddl,) = [s for s in statements if "CREATE TABLE IF NOT EXISTS reply_sources" in s]
    col = re.search(r"^\s*ts\s+([A-Za-z]+(?: [A-Za-z]+)?)\s", ddl, re.M).group(1)
    assert col.upper() == "DOUBLE PRECISION", col
    assert not re.search(r"\bREAL\b", " ".join(statements), re.I)


def test_a_reply_write_scheduled_before_an_erase_does_not_land_after_it(ledger_db):
    asyncio.run(rl.forget_user(UID))
    assert asyncio.run(rl.write(UID, _rec("r000000000000001"), 0)) is False                 # scheduled in the world before the erase
    assert ledger_db.rows("SELECT * FROM reply_sources") == []
    assert asyncio.run(rl.write(UID, _rec("r000000000000002"))) is True                    # a reply after the erase is a new conversation


def test_a_reply_write_waiting_for_a_connection_does_not_land_after_the_erase_finished(ledger_db, monkeypatch):
    real = db_compat.get_compat_db
    state = {"calls": 0, "gate": None}

    @contextlib.asynccontextmanager
    async def slow():
        state["calls"] += 1
        if state["calls"] == 1:                     # the write's own connection request waits (a pool under load)
            await state["gate"].wait()
        async with real() as db:
            yield db
    monkeypatch.setattr(db_compat, "get_compat_db", slow)

    async def go():
        state["gate"] = asyncio.Event()
        assert rl.schedule(UID, _rec("r000000000000003"))
        await asyncio.sleep(0)
        erase = asyncio.ensure_future(rl.forget_user(UID))
        await asyncio.sleep(0.01)
        assert not erase.done()                     # the erase queues behind the write in flight
        state["gate"].set()
        await asyncio.gather(erase, rl.flush())
    asyncio.run(go())
    assert ledger_db.rows("SELECT * FROM reply_sources") == []


def test_failed_writes_and_failed_erasures_are_logged_at_warning(svc, caplog):
    """No ``ledger_db``: ``get_compat_db`` has no pool, so every database call fails."""
    caplog.set_level(logging.DEBUG)
    assert asyncio.run(rl.write(UID, _rec("r000000000000004"))) is False
    assert asyncio.run(rl.forget_user(UID)) == 0
    assert asyncio.run(rl.forget_conversation(UID, "s1")) == 0
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING and r.name == "reply_ledger"]
    assert len(warned) == 3, [r.getMessage() for r in caplog.records]
