"""A night pass that read a member's rows BEFORE their erase writes nothing for them after it, and the erase removes what Zoe derived.

The night writers (dreaming, the nightly digest, weekly consolidation, portrait, card) run in their own process, where the in-memory
erase gate (#2569) never moves. Each pass records its start (``erase_watermark.night_cycle``) and every write checks the durable erase
watermark first (``erased_since_cycle``): a mark at or after the start = skip the member. Each scenario runs the REAL ``delete_user`` in
the middle of the pass (inside the fake model call, after the reads) and has a pass-through CONTROL showing the write landing.
``delete_user`` now also erases the derived tables (``member_derived_forget``). Synthetic placeholder text only.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import aiosqlite
import pytest

import db_compat

import erase_watermark as ew
import member_derived_forget
import memory_digest
import memory_service
import user_portrait
from test_erase_outruns_digest import OTHER, _erase, _rows, env  # noqa: F401 - the real delete_user over a fake collection
from test_memory_implicit_supersede import UID
from test_memory_open_loops_extract import _install, _LoopsDb, _loop

pytestmark = pytest.mark.ci_safe

_REAL_COMPAT = db_compat.get_compat_db      # captured before any test swaps it for a fake
INSIGHT = "User spends most weekend mornings working in the vegetable garden with their family"


def _no_check(monkeypatch):
    """The CONTROL: the night-pass check replaced by a pass-through (what every night writer did before)."""
    async def never(*_a, **_k):
        return False
    monkeypatch.setattr(ew, "erased_since_cycle", never)


def _night(coro_fn):
    """Run ``coro_fn()`` as a night pass (its start recorded)."""
    return asyncio.run(ew.night_cycle(coro_fn)())


class _Down:
    async def get(self, _uid):
        raise ConnectionError("store down")

    async def put(self, *_a):
        raise ConnectionError("store down")


def _erasing_llm(monkeypatch, svc, reply):
    """The model call of the pass: the member is erased WHILE it runs (the pass has already read their rows). The erase runs with
    the real connection factory, whatever fake the pass's own reads use."""
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            with monkeypatch.context() as m:
                m.setattr(db_compat, "get_compat_db", _REAL_COMPAT)
                await _erase(svc)
            return type("R", (), {"raise_for_status": lambda self: None,
                                  "json": lambda self: {"choices": [{"message": {"content": reply}}]}})()

    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", _Client)


# ── the palace writers (synthesis; music / the night digest / weekly review use the same gated ingest / review) ───────────────

@pytest.mark.parametrize("check", [True, False], ids=["watermark", "control-no-check"])
def test_a_synthesis_pass_erased_mid_call_stores_no_insight(env, monkeypatch, check):
    svc, col = env
    if not check:
        _no_check(monkeypatch)
    async def seed():
        for i in range(5):
            ref = await svc.ingest(f"User grows tomato variety number {i} in the garden", user_id=UID, source="digest",
                                   memory_type="profile", confidence=0.82, status="approved")
            col.rows[ref.id][1].update(status="approved", concept_tags="garden")
    asyncio.run(seed())
    monkeypatch.setattr(memory_digest, "_passes_quality_gate", lambda _t: True)
    class _Drawers:          # the pass's direct collection read, over the same fake (whose matcher has no $eq / $ne)
        def get(self, *, where=None, **kw):
            got = col.get(**kw)
            if where is None:
                return got
            keep = [i for i, m in enumerate(got["metadatas"]) if m.get("user_id") == UID and m.get("status") == "approved"
                    and m.get("source") != "synthesis"]
            return {k: [got[k][i] for i in keep] for k in ("ids", "documents", "metadatas")}

        def upsert(self, **kw):
            col.upsert(**kw)

    monkeypatch.setattr(memory_service, "leased_drawers", lambda _svc: _Drawers())
    _erasing_llm(monkeypatch, svc, INSIGHT)
    out = _night(lambda: memory_digest._synthesis_pass(UID))
    left = [d for d, _ in _rows(col, UID)]
    if not check:
        assert left == [INSIGHT] and out["synthesized"] == 1, "control: the insight from the erased member's rows lands"
        return
    assert left == [] and out["synthesized"] == 0


def test_outside_a_night_pass_the_check_is_a_no_op(env):
    svc, col = env

    async def go():
        await _erase(svc)
        return await ew.erased_since_cycle(UID, "t")

    assert asyncio.run(go()) is False, "a daytime write after the erase is a NEW write: only the in-memory gate and the store rules apply"


def test_an_unreadable_mark_skips_the_night_write_and_another_member_is_unaffected(env, caplog):
    caplog.set_level(logging.INFO)

    async def go():
        return await ew.erased_since_cycle(UID, "synthesis"), await ew.erased_since_cycle(OTHER, "synthesis")

    async def erased_one():
        ew.set_backend(ew.MemoryBackend())
        await ew.record(UID)
        return await go()

    assert _night(erased_one) == (True, False)
    ew.set_backend(_Down())
    assert _night(go) == (True, True), "fail closed: an unreadable mark skips every member"
    lines = [r.getMessage() for r in caplog.records if "ERASE_WATERMARK" in r.getMessage()]
    assert "ERASE_WATERMARK_PHASE_SKIP phase=synthesis" in lines and all(UID not in m and OTHER not in m for m in lines)


# ── archive_duplicate: a row read before the erase is never written back ───────────────────────────────────────────────────

@pytest.mark.parametrize("night", [True, False], ids=["night-pass", "daytime"])
def test_archive_duplicate_never_recreates_a_row_erased_after_it_was_read(env, monkeypatch, night):
    svc, col = env
    meta = {"user_id": UID, "status": "approved", "source": "turn_digest", "memory_type": "fact"}
    col.rows["dup"], col.rows["keep"] = ("User keeps bees", dict(meta)), ("User keeps bees", dict(meta))
    real_get, seen = svc.get, []

    async def get(mid):
        ref = await real_get(mid)
        seen.append(mid)
        if len(seen) == 2:
            await _erase(svc)                   # the weekly pass has read both rows; the erase lands before its write
        return ref

    monkeypatch.setattr(svc, "get", get)

    async def go():
        return await svc.archive_duplicate("dup", "keep", actor="weekly_consolidation")

    archived = _night(go) if night else asyncio.run(go())
    assert archived is False and _rows(col, UID) == [], "the erased row is not written back (an upsert would recreate it)"


# ── direct-SQL writers: portrait, user-model card, open loops ───────────────────────────────────────────────────────────────

_DERIVED_DDL = """
CREATE TABLE user_portraits (user_id TEXT PRIMARY KEY, portrait_text TEXT NOT NULL, portrait_version INTEGER DEFAULT 1,
    generated_from_memory_count INTEGER DEFAULT 0, last_generated TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE user_model_cards (user_id TEXT PRIMARY KEY, card_json TEXT NOT NULL, card_text TEXT NOT NULL, version TEXT NOT NULL,
    built_at TEXT NOT NULL, source_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE open_loops (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, loop_text TEXT NOT NULL, context TEXT,
    follow_up_hint TEXT, emotional_weight INTEGER DEFAULT 1, resolved BOOLEAN DEFAULT FALSE, resolved_at TIMESTAMP);
CREATE TABLE proactive_candidates (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE proactive_deliveries (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, cue_words TEXT NOT NULL DEFAULT '');
CREATE TABLE proactive_ledger_lines (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, line TEXT NOT NULL);
CREATE TABLE pending_suggestions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, offer_phrase TEXT NOT NULL);
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT);
"""


@pytest.fixture
def sqldb(monkeypatch, tmp_path):
    async def open_():
        db = await aiosqlite.connect(str(tmp_path / "zoe.db"))
        await db.executescript(_DERIVED_DDL)
        await db.commit()
        return db
    db = asyncio.run(open_())

    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    monkeypatch.setattr(member_derived_forget, "_db", ctx)       # the derived-store eraser's connection; the other erasers have no pool
    yield db
    asyncio.run(db.close())


def _count(db, table, user=UID):
    async def q():
        async with db.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user,)) as cur:
            return (await cur.fetchone())[0]
    return asyncio.run(q())


@pytest.mark.parametrize("check", [True, False], ids=["watermark", "control-no-check"])
def test_a_portrait_and_a_card_built_from_pre_erase_rows_are_not_written(env, sqldb, monkeypatch, check):
    svc, _col = env
    if not check:
        _no_check(monkeypatch)
    import user_model_card

    monkeypatch.setattr(user_portrait, "user_model_enabled", lambda _u: True)

    async def none(*_a, **_k):
        return ""

    monkeypatch.setattr(user_portrait, "load_portrait", none)

    async def go():
        rows = await svc.list_by_status(user_id=UID, status="approved")     # the pass reads ...
        await _erase(svc)                                                   # ... the member is erased ...
        await user_portrait._save_portrait(UID, "A synthetic portrait", len(rows), db=sqldb)   # ... then it writes
        return await user_model_card.rebuild_user_model_card(UID, db=sqldb)

    _night(go)
    assert (_count(sqldb, "user_portraits"), _count(sqldb, "user_model_cards")) == ((0, 0) if check else (1, 1))


@pytest.mark.parametrize("check", [True, False], ids=["watermark", "control-no-check"])
def test_open_loops_extracted_before_the_erase_are_not_inserted_after_it(env, monkeypatch, check):
    svc, _col = env
    if not check:
        _no_check(monkeypatch)
    import datetime as dt
    db = _LoopsDb([("The plumber still has not sent the quote for the kitchen sink",
                                 (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)).isoformat())])
    _install(monkeypatch, db, reply="unused")
    _erasing_llm(monkeypatch, svc, json.dumps([_loop("User is waiting on a quote from the plumber")]))
    out = _night(lambda: memory_digest._extract_open_loops(UID))
    if not check:
        assert out["inserted"] == 1, "control: a loop from the erased member's day is inserted after the erase"
        return
    assert (out["inserted"], out["status"], db.inserts) == (0, "erased_since_cycle", [])


# ── delete_user erases the derived stores, fail closed ──────────────────────────────────────────────────────────────────────

def test_delete_user_removes_every_derived_row_of_the_member_and_no_one_elses(env, sqldb):
    svc, _col = env

    async def seed():
        for u in (UID, OTHER):
            await sqldb.execute("INSERT INTO user_portraits (user_id, portrait_text) VALUES (?, 'p')", (u,))
            await sqldb.execute("INSERT INTO user_model_cards VALUES (?, '{}', 'c', 'v', 'now', 0)", (u,))
            await sqldb.execute("INSERT INTO open_loops (user_id, loop_text) VALUES (?, 'l')", (u,))
            for t, col in (("proactive_candidates", "text"), ("proactive_deliveries", "cue_words"),
                           ("proactive_ledger_lines", "line"), ("pending_suggestions", "offer_phrase")):
                await sqldb.execute(f"INSERT INTO {t} (id, user_id, {col}) VALUES (?, ?, 'x')", (f"{t}-{u}", u))
        await sqldb.commit()

    asyncio.run(seed())
    asyncio.run(_erase(svc))
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
        asyncio.run(_erase(svc))
    assert [d for d, _ in _rows(col, UID)] == ["User keeps hens"], "nothing of the palace was erased: the caller retries"
