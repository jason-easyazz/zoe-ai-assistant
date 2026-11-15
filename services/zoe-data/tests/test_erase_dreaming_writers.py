"""A night pass writes nothing for a member erased after it began - even when the erase races the write itself.

The night writers (dreaming, the nightly digest, weekly consolidation, portrait, card) run in their own process; ``delete_user`` runs in
the API process, and no in-process lock spans the two. So every night write goes through ONE rule, ``erase_watermark.guarded_write``:
check the durable erase mark right before the write, write, check again right after, and undo exactly what was written when the mark
moved past the pass start. ``delete_user`` records the mark before it erases anything, so a write that lands after the erase enumerated
its store is always seen by the second check.

Each writer is driven by its REAL code inside a night pass, with the REAL ``delete_user`` of a SECOND ``MemoryService`` over the same
store (the API process: its own per-user locks) injected at two points: (i) between the pre-check and the write, (ii) between the write
and the post-check. Both leave nothing for the member. The CONTROL injects (i) with both checks passed through: the row lands.
``delete_user`` now also erases the derived tables (``member_derived_forget``). Synthetic placeholder text only.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import types

import aiosqlite
import pytest

import db_compat
import erase_watermark as ew
import member_derived_forget
import memory_digest
import memory_service
import user_portrait
from memory_service import MemoryService
from test_erase_outruns_digest import OTHER, _rows, env  # noqa: F401 - a MemoryService over a fake collection
from test_memory_implicit_supersede import UID
from test_memory_open_loops_extract import _Cur, _install, _LoopsDb, _loop

pytestmark = pytest.mark.ci_safe

_REAL_COMPAT = db_compat.get_compat_db      # captured before any test swaps it for a fake
WHEN = ["between-check-and-write", "between-write-and-recheck", "control-no-check"]


@pytest.fixture
def api(env, monkeypatch):
    """The API process: its own MemoryService (own per-user locks) over the SAME store, and its real delete_user."""
    svc, col = env
    other = MemoryService(data_dir="/nonexistent/zoe-test-erase-dreaming-api")
    other._collection, other._audit_collection = svc._collection, svc._audit_collection
    other._append_audit, other.search = svc._append_audit, svc.search

    async def erase():
        with monkeypatch.context() as m:        # the API process's erasers use the real connection factory, not the night pass's fakes
            m.setattr(db_compat, "get_compat_db", _REAL_COMPAT)
            await other.delete_user(UID, actor="test", reason="synthetic erase")
    return svc, col, erase


def _inject(monkeypatch, erase, phase, when):
    """Run the API erase at ``when`` of the first ``guarded_write`` of ``phase`` for the member (call 1 = pre-check, 2 = post-check)."""
    real = ew.erased_since_cycle
    calls = {"n": 0}

    async def wrapped(uid, ph):
        if uid != UID or ph != phase:
            return await real(uid, ph)
        calls["n"] += 1
        if when == "between-write-and-recheck" and calls["n"] == 2:
            await erase()
        out = False if when == "control-no-check" else await real(uid, ph)
        if when in ("between-check-and-write", "control-no-check") and calls["n"] == 1:
            await erase()                       # the check has already answered "not erased": the erase lands right after it
        return out
    monkeypatch.setattr(ew, "erased_since_cycle", wrapped)
    return calls


def _night(coro_fn):
    return asyncio.run(ew.night_cycle(coro_fn)())


def _expect(when, left):
    if when == "control-no-check":
        assert left >= 1, "control: with no check the night write lands after the erase"
    else:
        assert left == 0, when


class _Down:
    async def get(self, _uid):
        raise ConnectionError("store down")

    async def put(self, *_a):
        raise ConnectionError("store down")


# ── palace writers: ingest (synthesis, music, the night digest), review (weekly), archive_duplicate (weekly) ────────────────

@pytest.mark.parametrize("when", WHEN)
def test_ingest(api, monkeypatch, when):
    svc, col, erase = api
    _inject(monkeypatch, erase, "synthesis", when)
    _night(lambda: svc.ingest("User keeps a small vegetable garden behind the house", user_id=UID, source="synthesis",
                              memory_type="insight", confidence=0.85, status="approved"))
    _expect(when, len(_rows(col, UID)))


@pytest.mark.parametrize("when", WHEN)
def test_archive_duplicate(api, monkeypatch, when):
    svc, col, erase = api
    meta = {"user_id": UID, "status": "approved", "source": "turn_digest", "memory_type": "fact"}
    col.rows["dup"], col.rows["keep"] = ("User keeps bees", dict(meta)), ("User keeps bees", dict(meta))
    _inject(monkeypatch, erase, "archive_duplicate", when)
    _night(lambda: svc.archive_duplicate("dup", "keep", actor="weekly_consolidation"))
    _expect(when, len(_rows(col, UID)))


@pytest.mark.parametrize("when", WHEN)
def test_review_edit(api, monkeypatch, when):
    svc, col, erase = api
    meta = {"user_id": UID, "status": "approved", "source": "synthesis", "memory_type": "fact",     # inferred: the weekly pass may edit it
            "authority": "inferred", "authority_class": "inferred"}
    col.rows["old"] = ("User keeps three hens", dict(meta))
    _inject(monkeypatch, erase, "consolidation", when)
    _night(lambda: svc.review("old", decision="edit", edits="User keeps four hens", actor="consolidation"))
    _expect(when, len(_rows(col, UID)))


def test_a_row_erased_after_it_was_read_is_never_written_back_by_day_either(api, monkeypatch):
    """Daytime (same process): the erase holds the per-user lock, and archive_duplicate / review re-read the row under it."""
    svc, col, _erase_api = api
    meta = {"user_id": UID, "status": "approved", "source": "turn_digest", "memory_type": "fact"}
    col.rows["dup"], col.rows["keep"] = ("User keeps bees", dict(meta)), ("User keeps bees", dict(meta))
    real_get, seen = svc.get, []

    async def get(mid):
        ref = await real_get(mid)
        seen.append(mid)
        if len(seen) == 2:
            await svc.delete_user(UID, actor="test", reason="synthetic erase")
        return ref

    monkeypatch.setattr(svc, "get", get)
    assert asyncio.run(svc.archive_duplicate("dup", "keep", actor="weekly_consolidation")) is False and _rows(col, UID) == []


# ── SQL writers: portrait, user-model card, conversation notes, proactive selector, anticipation (one SQLite file) ─────────────

_DDL = """
CREATE TABLE user_portraits (user_id TEXT PRIMARY KEY, portrait_text TEXT NOT NULL, portrait_version INTEGER DEFAULT 1,
    generated_from_memory_count INTEGER DEFAULT 0, last_generated TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE user_model_cards (user_id TEXT PRIMARY KEY, card_json TEXT NOT NULL, card_text TEXT NOT NULL, version TEXT NOT NULL,
    built_at TEXT NOT NULL, source_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE open_loops (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, loop_text TEXT NOT NULL);
CREATE TABLE proactive_candidates (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, source_ref TEXT NOT NULL,
    text TEXT NOT NULL, hint TEXT NOT NULL DEFAULT '', salience REAL NOT NULL DEFAULT 0, on_open INTEGER NOT NULL DEFAULT 1,
    cue_words TEXT NOT NULL DEFAULT '', expires_at TEXT NOT NULL, cooldown_until TEXT, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, UNIQUE (user_id, kind, source_ref));
CREATE TABLE proactive_deliveries (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, cue_words TEXT NOT NULL DEFAULT '');
CREATE TABLE proactive_ledger_lines (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, line TEXT NOT NULL DEFAULT '');
CREATE TABLE pending_suggestions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '',
    action_type TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', list_type TEXT, when_hint TEXT, amount_hint TEXT,
    offer_phrase TEXT NOT NULL DEFAULT '', pre_filled_slots TEXT, created_at TEXT NOT NULL DEFAULT '',
    turns_elapsed INTEGER NOT NULL DEFAULT 0, expire_after_turns INTEGER NOT NULL DEFAULT 2, resolved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT);
"""


@pytest.fixture
def sqldb(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path

    import conversation_notes

    versions = Path(memory_service.__file__).parent / "alembic" / "versions"
    spec = importlib.util.spec_from_file_location("mig_0074", next(versions.glob("0074_*.py")))
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    async def open_():
        db = await aiosqlite.connect(str(tmp_path / "zoe.db"))
        await db.executescript(_DDL)
        await db.execute(mig.NOTES)                                  # the REAL 0074 table
        await db.commit()
        return db
    db = asyncio.run(open_())

    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    monkeypatch.setattr(member_derived_forget, "_db", ctx)            # the API erase's derived-store eraser ...
    monkeypatch.setattr(conversation_notes, "_db", ctx)              # ... and the notes store (pass and erase alike)
    db.ctx = ctx
    yield db
    asyncio.run(db.close())


def _count(db, table, user=UID):
    async def q():
        async with db.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user,)) as cur:
            return (await cur.fetchone())[0]
    return asyncio.run(q())


@pytest.mark.parametrize("when", WHEN)
def test_portrait(api, sqldb, monkeypatch, when):
    _svc, _col, erase = api
    _inject(monkeypatch, erase, "portrait", when)
    _night(lambda: user_portrait._save_portrait(UID, "A synthetic portrait", 3, db=sqldb))
    _expect(when, _count(sqldb, "user_portraits"))


@pytest.mark.parametrize("when", WHEN)
def test_user_model_card(api, sqldb, monkeypatch, when):
    _svc, _col, erase = api
    import user_model_card

    monkeypatch.setattr(user_portrait, "user_model_enabled", lambda _u: True)

    async def none(*_a, **_k):
        return ""
    monkeypatch.setattr(user_portrait, "load_portrait", none)
    _inject(monkeypatch, erase, "user_model_card", when)
    _night(lambda: user_model_card.rebuild_user_model_card(UID, db=sqldb))
    _expect(when, _count(sqldb, "user_model_cards"))


@pytest.mark.parametrize("when", WHEN)
def test_conversation_notes(api, sqldb, monkeypatch, when):
    _svc, _col, erase = api
    import conversation_notes as cn

    convo = cn.Convo(key="ep-1", started=1.0e9, ended=1.0e9 + 600, turns=4, turn_ids=["m-1"])
    note = cn.Note(summary="Talked about the garden plans", topics=["garden"], mood="", open_threads=[], follow_up="")
    _inject(monkeypatch, erase, "conversation_notes", when)
    # gate_held: the in-process gate is not the subject here (the API erase is another process's)
    _night(lambda: cn.save(UID, "", convo, note, source="chat", status="active", gate_held=True))
    _expect(when, _count(sqldb, "conversation_notes"))


@pytest.mark.parametrize("when", WHEN)
def test_proactive_selector(api, sqldb, monkeypatch, when):
    _svc, _col, erase = api
    import restraint
    from proactive import selector

    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "1")
    monkeypatch.setattr(db_compat, "get_compat_db", sqldb.ctx)

    async def nothing(*_a, **_k):
        return ()
    cand = types.SimpleNamespace(kind="loop", source_ref="l-1", text="Ask how the quote went", hint="", salience=0.7, cues="quote",
                                 expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2))
    monkeypatch.setattr(selector, "_gather", nothing)
    monkeypatch.setattr(selector, "score_all", lambda *a: [cand])
    monkeypatch.setattr(selector, "rank", lambda s: list(s))
    monkeypatch.setattr(restraint, "mode", lambda: "off")

    async def zero(*_a, **_k):
        return 0
    monkeypatch.setattr(restraint, "erase_user", zero)
    _inject(monkeypatch, erase, "proactive_selector", when)
    _night(lambda: selector.select_for_user(UID))
    _expect(when, _count(sqldb, "proactive_candidates"))


@pytest.mark.parametrize("when", WHEN)
def test_anticipation(api, sqldb, monkeypatch, when):
    _svc, _col, erase = api
    import anticipation as an

    now = dt.datetime.now(dt.timezone.utc)
    offer = types.SimpleNamespace(key="ev-1|start", slots={}, surface_from=now, title="Dentist", kind="wake", expires_at=now)

    async def empty(*_a, **_k):
        return []

    async def adult(*_a, **_k):
        return True
    monkeypatch.setattr(an, "mode", lambda: "on")
    monkeypatch.setattr(an, "is_member", lambda _u: True)
    monkeypatch.setattr(an, "_events", empty)
    monkeypatch.setattr(an, "_usual_alarms", empty)
    monkeypatch.setattr(an, "_rows", empty)
    monkeypatch.setattr(an, "_is_adult", adult)
    monkeypatch.setattr(an, "candidates", lambda *a, **k: [offer])
    monkeypatch.setattr(an, "phrase", lambda *a, **k: "Want me to wake you earlier?")
    _inject(monkeypatch, erase, "anticipation", when)
    _night(lambda: an.generate_for_user(UID, now=now, db=sqldb))
    _expect(when, _count(sqldb, "pending_suggestions"))


# ── open loops (the compat-layer fake), the now card (its own store) ─────────────────────────────────────────────────────────

class _LoopsStore(_LoopsDb):
    """The open-loops fake, plus the DELETEs an undo or an erase sends (an open_loops DELETE removes the member's inserted rows)."""

    def execute(self, sql, params=()):
        head = " ".join(sql.split())
        if head.startswith("DELETE"):
            if "FROM open_loops" in head:
                text = params[1] if len(params) > 1 else None
                self.inserts = [p for p in self.inserts if not (p[0] == params[0] and (text is None or p[1] == text))]
            return _Cur()
        return super().execute(sql, params)

    async def commit(self):
        pass


@pytest.mark.parametrize("when", WHEN)
def test_open_loops(api, monkeypatch, when):
    _svc, _col, erase = api
    db = _LoopsStore([("The plumber still has not sent the quote for the kitchen sink",
                       (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)).isoformat())])
    _install(monkeypatch, db, reply=json.dumps([_loop("User is waiting on a quote from the plumber")]))

    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    monkeypatch.setattr(member_derived_forget, "_db", ctx)           # the API erase reaches the same store
    _inject(monkeypatch, erase, "open_loops", when)
    _night(lambda: memory_digest._extract_open_loops(UID))
    _expect(when, len([p for p in db.inserts if p[0] == UID]))


@pytest.mark.parametrize("when", WHEN)
def test_now_card(api, monkeypatch, when):
    _svc, _col, erase = api
    import now_card
    from test_now_card import DictStore

    store = DictStore()
    prev = now_card.set_store(store)
    monkeypatch.setattr(now_card, "mode", lambda: "on")
    monkeypatch.setattr(now_card, "build_eligible", lambda *a, **k: "")

    async def gather(*_a, **_k):
        return object(), []
    monkeypatch.setattr(now_card, "gather", gather)
    monkeypatch.setattr(now_card, "compose", lambda *a: [("thread", "the garden plans", "r-1")])
    _inject(monkeypatch, erase, "now_card", when)
    try:
        _night(lambda: now_card.rebuild(UID))
    finally:
        now_card.set_store(prev)
    _expect(when, int(UID in store.rows))


# ── the rule itself ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_outside_a_night_pass_the_rule_is_a_plain_write(env):
    async def go():
        await ew.record(UID)
        wrote = []

        async def write():
            wrote.append(1)
            return "x"

        async def undo(_x):
            raise AssertionError("never undone outside a night pass")
        return await ew.guarded_write(UID, "t", write, undo), wrote

    assert asyncio.run(go()) == ("x", [1])


def test_an_unreadable_mark_skips_the_write_a_failed_undo_raises_and_logs_hold_no_id(env, caplog):
    caplog.set_level(logging.INFO)
    ew.set_backend(_Down())

    async def never(*_a):
        raise AssertionError("not written on an unreadable mark")
    assert _night(lambda: ew.guarded_write(UID, "synthesis", never, never)) is None

    ew.set_backend(ew.MemoryBackend())

    async def write_then_erase():
        await ew.record(UID)                          # the erase lands while the write runs
        return "x"

    async def broken_undo(_x):
        raise ConnectionError("store down")
    with pytest.raises(ConnectionError):
        _night(lambda: ew.guarded_write(UID, "synthesis", write_then_erase, broken_undo))
    lines = [r.getMessage() for r in caplog.records if "ERASE_WATERMARK" in r.getMessage()]
    assert "ERASE_WATERMARK_COMPENSATE_FAILED phase=synthesis code=ConnectionError" in lines
    assert all(UID not in m for m in lines)


# ── delete_user erases the derived stores, fail closed ──────────────────────────────────────────────────────────────────────

def test_delete_user_removes_every_derived_row_of_the_member_and_no_one_elses(env, sqldb):
    svc, _col = env

    async def seed():
        for u in (UID, OTHER):
            await sqldb.execute("INSERT INTO user_portraits (user_id, portrait_text) VALUES (?, 'p')", (u,))
            await sqldb.execute("INSERT INTO user_model_cards VALUES (?, '{}', 'c', 'v', 'now', 0)", (u,))
            await sqldb.execute("INSERT INTO open_loops (user_id, loop_text) VALUES (?, 'l')", (u,))
            await sqldb.execute("INSERT INTO proactive_candidates (id, user_id, kind, source_ref, text, expires_at, created_at, updated_at)"
                                " VALUES (?, ?, 'loop', 'r', 'x', 'e', 'c', 'u')", (f"c-{u}", u))
            for t, col in (("proactive_deliveries", "cue_words"), ("proactive_ledger_lines", "line"), ("pending_suggestions", "offer_phrase")):
                await sqldb.execute(f"INSERT INTO {t} (id, user_id, {col}) VALUES (?, ?, 'x')", (f"{t}-{u}", u))
        await sqldb.commit()

    asyncio.run(seed())
    asyncio.run(svc.delete_user(UID, actor="test", reason="synthetic erase"))
    for t in member_derived_forget.TABLES:
        assert (_count(sqldb, t, UID), _count(sqldb, t, OTHER)) == (0, 1), t


def test_a_derived_store_that_cannot_be_cleared_fails_the_delete(env, monkeypatch):
    svc, col = env
    asyncio.run(svc.ingest("User keeps hens", user_id=UID, source="digest", memory_type="profile", confidence=0.82,
                           status="approved"))

    @contextlib.asynccontextmanager
    async def down():
        raise ConnectionError("store down")
        yield  # pragma: no cover

    monkeypatch.setattr(member_derived_forget, "_db", down)
    with pytest.raises(memory_service.MemoryServiceError, match="derived-store erasure failed"):
        asyncio.run(svc.delete_user(UID, actor="test", reason="synthetic erase"))
    assert [d for d, _ in _rows(col, UID)] == ["User keeps hens"], "nothing of the palace was erased: the caller retries"


# ── the night-write undo inside the memory lock skips the registry step; the member erase clears the registry itself ──────────

def test_a_night_write_undone_under_the_lock_leaves_no_palace_and_no_registry_row(env, monkeypatch, tmp_path):
    """erase_rows(lock_held=True) (a night write undoing itself inside ingest) must NOT take the erase gate under the memory lock, so
    it skips memory_value_registry.erase_for_rows; the member erase that raced it clears the whole registry (forget_user)."""
    import memory_value_registry as reg
    import test_memory_value_registry as tmvr

    svc, col = env
    member = tmvr.A
    path = str(tmp_path / "zoe.db")
    db = tmvr._Sqlite(path)
    db.conn.executescript(tmvr.PEOPLE + ";")
    db.conn.execute("INSERT INTO people (id, user_id, name, deleted) VALUES (?, ?, 'Juno', 0)", (tmvr.JUNO, member))
    db.conn.commit()
    import sqlalchemy as sa
    tmvr._migrate(sa.create_engine(f"sqlite:///{path}"))                # the REAL migration 0080

    @contextlib.asynccontextmanager
    async def regdb():
        yield db
    monkeypatch.setattr(reg, "_db", regdb)                       # the registry's own store, for the pass and the erase alike
    reg.reset_state()
    rows = lambda: db.q("SELECT COUNT(*) FROM memory_value_registry WHERE user_id = ?", (member,))[0][0]  # noqa: E731
    assert asyncio.run(reg.record(member, tmvr.JUNO, "age", superseded=[tmvr._age(7)], asserted=tmvr._age(8), said_at=tmvr.T0,
                                  by_claim=tmvr.C(1), row_ids=[tmvr.R(member, "r1")], now=tmvr.T0)) == 2
    assert rows() == 2, "control: the registry holds the member's entries before the erase"

    other = MemoryService(data_dir="/nonexistent/zoe-test-erase-dreaming-api")
    other._collection, other._audit_collection = svc._collection, svc._audit_collection
    other._append_audit, other.search = svc._append_audit, svc.search
    real_check, calls, undo_calls, registry_calls = ew.erased_since_cycle, {"n": 0}, [], []

    async def check(uid, ph):
        out = await real_check(uid, ph)
        if uid == member and ph == "synthesis":
            calls["n"] += 1
            if calls["n"] == 1:
                await other.delete_user(member, actor="test", reason="synthetic erase")   # the API erase, right after the pre-check
        return out
    monkeypatch.setattr(ew, "erased_since_cycle", check)
    real_erase_rows = svc.erase_rows

    async def erase_rows(user_id, ids, **kw):
        undo_calls.append(kw.get("lock_held"))
        return await real_erase_rows(user_id, ids, **kw)
    monkeypatch.setattr(svc, "erase_rows", erase_rows)
    real_for_rows = reg.erase_for_rows

    async def for_rows(*a, **k):
        registry_calls.append(1)
        return await real_for_rows(*a, **k)
    monkeypatch.setattr(reg, "erase_for_rows", for_rows)

    _night(lambda: svc.ingest("User keeps a small vegetable garden behind the house", user_id=member, source="synthesis",
                              memory_type="insight", confidence=0.85, status="approved"))
    assert undo_calls == [True], "the racing write was undone through erase_rows(lock_held=True)"
    assert registry_calls == [], "the undo under the memory lock never takes the erase gate (no erase_for_rows)"
    assert _rows(col, member) == [] and rows() == 0, "no palace row and no registry row survive"
