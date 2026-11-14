"""QA round 10a: after a TOPIC forget, the forget COMMAND itself must never be re-mined or handed back.

The leak (shape only): "my brother <name> lives in <place>" -> "forget that I told you about my brother" (a topic forget: no
ledger entry) -> after an idle drain, in a NEW session, "do I have a brother?" got "I do have a record of a past conversation about
him". Idle consolidation read the command's own saved row (not off the record, not in the ledger), the extractor paraphrased it
into a row about the brother, and the household recall block served it.

The fix: a forget command (``turn_forget.is_forget_instruction``: the union of the existing detectors) is saved off the record
(``routers/chat._save_chat_message``) AND skipped by ``memory_digest._skip_forgotten_turns`` (every transcript reader: idle
consolidation, the digest loaders, open loops, the turn digest). Each claim has a negative control. Synthetic names only."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import flue_fastpath_context as ffc
import memory_digest as md
import memory_forgotten as mf
import memory_idle_consolidation as mic
import turn_forget
from forgotten_support import OTHER, USER, ledger_env  # noqa: F401 - fixture + inputs

pytestmark = pytest.mark.ci_safe

TEACH = "my brother rowan lives in darwin, we talk most weekends"
TOPIC_FORGET = "forget that i told you about my brother"
ENTITY_FORGET = "forget everything about rowan"
LAST_FORGET = "forget that"
RETEACH = "my brother rowan just moved to cairns"
QUESTION = "do i have a brother"
OTHER_FACT = "my sister ione is a nurse in perth"
MINED = "User told Zoe about their brother before"        # what the extractor made of the command


def run(coro):
    return asyncio.run(coro)


# ── the detector: the union of the existing ones, nothing wider ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [TOPIC_FORGET, ENTITY_FORGET, LAST_FORGET, "forget what i said about being lonely",
                                  "no, don't remember that, forget that"])
def test_every_forget_command_shape_is_a_forget_instruction(text):
    assert turn_forget.is_forget_instruction(text)


@pytest.mark.parametrize("text", [TEACH, RETEACH, QUESTION, OTHER_FACT, "don't forget that my brother rowan's birthday is in may",
                                  "i keep telling my brother rowan to forget about it but he never does, he lives in darwin now and "
                                  "we talk most weekends about everything that went on"])
def test_a_fact_a_question_a_teach_cue_or_a_long_story_is_not(text):
    assert not turn_forget.is_forget_instruction(text)


# ── the transcript save: the command row is off the record ────────────────────────────────────────────────────────────────────
def _saved_meta(monkeypatch, rows):
    import db_pool
    from routers import chat

    db = MagicMock(execute=AsyncMock(), commit=AsyncMock(), execute_fetchall=AsyncMock(return_value=[]))

    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    for role, text in rows:
        run(chat._save_chat_message("sess-a", role, text, user_id=USER))
    inserts = [c.args[1] for c in db.execute.await_args_list if c.args[0].startswith("INSERT INTO chat_messages")]
    return [json.loads(p[4]) if p[4] else {} for p in inserts]


def test_the_saved_forget_command_row_is_off_the_record_and_a_fact_row_is_not(monkeypatch):
    metas = _saved_meta(monkeypatch, [("user", TEACH), ("user", TOPIC_FORGET), ("user", ENTITY_FORGET), ("user", OTHER_FACT)])
    assert [m.get("off_record") for m in metas] == [None, True, True, None]


def test_control_without_the_detector_the_command_row_is_saved_on_the_record(monkeypatch):
    monkeypatch.setattr(turn_forget, "is_forget_instruction", lambda _t: False)
    metas = _saved_meta(monkeypatch, [("user", TOPIC_FORGET)])
    assert metas[0].get("off_record") is None                       # the defect: every transcript reader can mine it


# ── the fast-path context: the command (and Zoe's reply to it) never re-enters the brain ──────────────────────────────────────
def test_fastpath_context_never_carries_the_forget_exchange(monkeypatch):
    metas = _saved_meta(monkeypatch, [("user", TOPIC_FORGET)])
    reply = {"user_id": USER}
    rows = [("user", QUESTION, json.dumps({"user_id": USER}), "r3"),
            ("assistant", "Done - I've forgotten what you told me about that.", json.dumps(reply), "r2"),
            ("user", TOPIC_FORGET, json.dumps(metas[0]), "r1")]
    assert ffc.render(ffc.select_unseen(rows, USER, QUESTION, [])) == ""
    # control: the same row as it was saved before the fix hands the topic straight back to the brain
    rows[2] = ("user", TOPIC_FORGET, json.dumps({"user_id": USER}), "r1")
    assert "brother" in ffc.render(ffc.select_unseen(rows, USER, QUESTION, []))


# ── the transcript readers: a command row saved before the fix is still never mined ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_skip_filter_drops_the_command_and_keeps_facts_and_a_reteach(ledger_env, caplog):
    with caplog.at_level(logging.DEBUG):
        kept = await md._skip_forgotten_turns(USER, [TOPIC_FORGET, OTHER_FACT, RETEACH], "digest")
    assert kept == [OTHER_FACT, RETEACH]                           # an unforgotten person and a later re-teach are unaffected
    assert "forget-command" in caplog.text and "brother" not in caplog.text and "rowan" not in caplog.text


@pytest.mark.asyncio
async def test_entity_forget_still_skips_the_named_turn_and_a_reteach_releases_it(ledger_env):
    await mf.add(USER, "Rowan")
    assert await md._skip_forgotten_turns(USER, [TEACH, OTHER_FACT], "digest") == [OTHER_FACT]
    assert await md._skip_forgotten_turns(OTHER, [TEACH], "digest") == [TEACH]       # another member is untouched
    await mf.release(USER, "Rowan")                                                    # what an explicit re-teach does
    mf.reset_state()
    assert await md._skip_forgotten_turns(USER, [RETEACH], "digest") == [RETEACH]


@pytest.mark.asyncio
async def test_ledger_unreadable_serves_nothing_for_a_named_person_and_logs_the_code(ledger_env, monkeypatch, caplog):
    await mf.add(USER, "Rowan")
    mf.reset_state()

    async def down(*_a, **_k):
        raise ConnectionError("ledger down")
    monkeypatch.setattr(ledger_env, "active_hashes", down)
    with caplog.at_level(logging.DEBUG), pytest.raises(mf.LedgerUnavailable):
        await md._skip_forgotten_turns(USER, [TOPIC_FORGET, TEACH], "digest")
    assert "LedgerUnavailable" in caplog.text and "rowan" not in caplog.text.lower()


class _Row(dict):
    pass


class _Conn:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    async def fetch(self, q, *a):
        # the real SQL leaves off_record rows out; the fake does the same so the test holds the code to it
        return [r for r in self.rows if '"off_record": true' not in (r.get("metadata") or "")]

    async def execute(self, q, *a):
        self.executed.append((q, a))


def _idle(monkeypatch, rows, extract):
    import expert_dispatch
    import memory_quality
    import memory_service

    seen = {"extract": [], "ingest": []}

    async def _extract(text):
        seen["extract"].append(text)
        return extract(text)

    async def _ingest(svc, text, **kw):
        seen["ingest"].append(text)
        return "stored"
    conn = _Conn(rows)

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(md, "_extract_facts_with_gemma", _extract)
    monkeypatch.setattr(memory_quality, "is_storable_fact", lambda t: (True, ""))
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: object())
    monkeypatch.setattr(expert_dispatch, "_ingest_or_supersede", _ingest)
    stored = run(mic.consolidate_session("sess-a", USER, get_ctx=lambda: _Ctx()))
    return stored, seen


def _transcript(command_meta: dict) -> list:
    u = json.dumps({"user_id": USER})
    return [_Row(role="user", content=TEACH, at="t1", metadata=json.dumps({"user_id": USER, "off_record": True})),  # the retracted turn
            _Row(role="assistant", content="That's lovely.", at="t2", metadata=u),
            _Row(role="user", content=TOPIC_FORGET, at="t3", metadata=json.dumps(command_meta)),
            _Row(role="assistant", content="Done - I've forgotten what you told me about that.", at="t4", metadata=u),
            _Row(role="user", content=OTHER_FACT, at="t5", metadata=u)]


def _model(text):
    out = [{"fact": "User's sister Ione is a nurse in Perth"}] if "ione" in text else []
    return out + ([{"fact": MINED}] if "brother" in text else [])


def test_idle_consolidation_never_mints_a_row_from_an_old_unflagged_command_row(ledger_env, monkeypatch):
    stored, seen = _idle(monkeypatch, _transcript({"user_id": USER}), _model)   # saved before the fix: on the record
    assert MINED not in seen["ingest"] and all("brother" not in t for t in seen["extract"])
    assert seen["ingest"] == ["User's sister Ione is a nurse in Perth"]          # the unforgotten fact still lands


def test_control_without_the_skip_the_command_is_mined_into_a_row_about_the_brother(ledger_env, monkeypatch):
    monkeypatch.setattr(turn_forget, "is_forget_instruction", lambda _t: False)
    stored, seen = _idle(monkeypatch, _transcript({"user_id": USER}), _model)
    assert MINED in seen["ingest"]                                                 # the round-10a defect, reproduced


# ── the existence question: with no minted row, the household block has nothing to show ───────────────────────────────────────
def test_existence_question_finds_nothing_once_the_command_is_not_mined():
    import memory_gate

    clean = "- User's sister Ione is a nurse in Perth\n- User is a librarian"
    assert not memory_gate.packet_touches_question(QUESTION, clean)       # the block is not sent: nothing about him
    assert memory_gate.packet_touches_question(QUESTION, clean + f"\n- {MINED}")   # control: the minted row WAS what it showed


# ── the PRODUCTION SQL of every reader the fix relies on asks for the off-the-record exclusion (the fakes above apply it themselves) ──
class _RecDb:
    """Records every SQL a reader issues; returns no rows."""

    def __init__(self):
        self.sql: list = []

    async def execute(self, sql, *_a, **_k):
        self.sql.append(sql)
        cur = MagicMock()
        cur.fetchall = AsyncMock(return_value=[])
        return cur

    async def fetch(self, sql, *_a, **_k):
        self.sql.append(sql)
        return []

    async def execute_fetchall(self, sql, *_a, **_k):
        self.sql.append(sql)
        return []


def _recording_pool(monkeypatch):
    import db_pool

    rec = _RecDb()

    @contextlib.asynccontextmanager
    async def ctx():
        yield rec
    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    return rec


def _user_turn_reads(rec) -> list:
    return [q for q in rec.sql if "chat_messages" in q and "role = 'user'" in q]


def _asks_off_record(q: str) -> bool:
    import memory_provenance

    return memory_provenance.OFF_RECORD_JSON_MARK in q and "NOT LIKE" in q


@pytest.mark.parametrize("reader", ["idle_consolidation", "digest_today", "digest_day", "exact_words_backfill",
                                    "exact_words_candidates", "pronoun_lookback"])
def test_each_transcript_readers_production_sql_excludes_off_record_rows(reader, monkeypatch):
    import exact_words
    import memory_extractor

    rec = _recording_pool(monkeypatch)
    calls = {
        "idle_consolidation": lambda: mic._fetch_transcript_rows(rec, "sess-a", None),
        "digest_today": lambda: md._load_todays_messages(USER, rec),
        "digest_day": lambda: md.load_day_messages(USER, "2026-11-14T00:00:00+00:00", "2026-11-15T00:00:00+00:00", rec),
        "exact_words_backfill": lambda: exact_words._backfill_page(USER, 36, None, 50),
        "exact_words_candidates": lambda: exact_words._transcript_candidates(USER, "brother"),
        "pronoun_lookback": lambda: memory_extractor._load_recent_user_messages(USER, "sess-a", QUESTION),
    }
    run(calls[reader]())
    if reader == "idle_consolidation":                      # its SQL has no role filter: it reads both sides, then keeps user turns
        reads = [q for q in rec.sql if "chat_messages" in q]
    else:
        reads = _user_turn_reads(rec)
    assert reads and all(_asks_off_record(q) for q in reads)


def test_fastpath_context_reads_the_metadata_and_filters_off_record_in_code(monkeypatch):
    """The fast-path reader keeps the off-record rows in its SQL ON PURPOSE: it must see a skipped user row to also skip the reply
    that restates it (``select_unseen``'s ``skip_reply``). Its production SQL must therefore return ``metadata``; the filter itself is
    the real ``select_unseen`` (driven above with the metadata ``_save_chat_message`` writes)."""
    rec = _recording_pool(monkeypatch)
    run(ffc._fetch_rows("sess-a"))
    assert rec.sql and "metadata" in rec.sql[0]
    rows = [("user", QUESTION, json.dumps({"user_id": USER}), "r2"),
            ("user", TOPIC_FORGET, json.dumps({"user_id": USER, "off_record": True}), "r1")]
    assert all(t != TOPIC_FORGET for _r, t in ffc.select_unseen(rows, USER, "next", []))


def test_control_the_sql_check_sees_a_reader_that_stops_asking(monkeypatch):
    monkeypatch.setattr(md, "_off_record_sql", lambda alias="cm": "1 = 1")      # the digest binds it at import
    rec = _recording_pool(monkeypatch)
    run(md._load_todays_messages(USER, rec))
    reads = _user_turn_reads(rec)
    assert reads and not any(_asks_off_record(q) for q in reads)          # the instrument is not blind
