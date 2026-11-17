"""THE CLASS TEST for a forget's steps (PR #2574 review rounds 9-13): every forget ENTRY POINT x every STEP, a failure injected at
that step (once, and twice), then the same request again.

The bug class. A forget is several steps (reject the rows, physically erase them, flag the transcript, erase the exact-words entries,
the night mind's observations, the conversation notes, the stored user-model card, the value-registry entries, a topic's pending
offers). When step N failed, the retry had to REDISCOVER what steps 1..N-1 had already removed, and could not: a rejected row is no
longer listed, a flagged transcript turn no longer resolves the words, an erased entry no longer names its turn. Five review rounds
found that shape at five call sites. The fix is one write-ahead record (``forget_record``): resolve every target id first, write it
down (ids only), work from it, confirm only when every step is done.

For each case: the first reply is NOT a confirmation when the injected failure fired; repeating the request (at most three more
times) ends in a confirmation; and then every artefact of the target turn is gone - rows hidden AND erased, transcript flagged, the
exact-words entry, the observation (and its thread re-derived without it), the conversation note, the stored card, the registry
entry (and, for the topic forget, the pending offer). Another turn's data is never touched (the control).

On the new code two structural tests pin the matrix to the code: the step list IS ``forget_record.STEPS`` and the entry-point list
IS ``forget_record.ENTRY_POINTS`` (each read from the code; every ``forget_record.forget(code=...)`` call site in services/zoe-data
names one), so a new step or a new entry point without a seam / driver here fails. Synthetic placeholder words only.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest

import db_compat
import db_pool
import exact_words as xw
import forget_topic
import intent_router
import memory_provenance as mp
import memory_space
import night_store
import pending_suggestions
import provenance_answers as pa
import user_erase_gate
from db_pool import _Cursor, _ExecResult
from forgotten_support import ledger_env  # noqa: F401 - fixture
from test_forget_by_turn_id import FACTS, COPIES, KEPT, UID, _approved, _plant, _xw_rows, world  # noqa: F401 - world is a fixture

try:
    import forget_record
except ImportError:          # the old code: no record; the matrix still runs (and shows what the per-site code missed)
    forget_record = None

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[1]
ADMIN = "demo_adm_0000id02"
OTHER_MID = "m-control-z"
STEPS_FALLBACK = ("reject", "erase_rows", "transcript", "exact_words", "night_mind", "notes", "card", "registry", "offers")
STEPS = tuple(forget_record.STEPS) if forget_record is not None else STEPS_FALLBACK


def run(coro):
    return asyncio.run(coro)


class Injected(Exception):
    """The injected store failure. NOT a RuntimeError: a process that never had a pool treats a RuntimeError from a store as "no
    store to erase" (``memory_value_registry._no_store``), which would hide the injection instead of failing the step."""


def _mig(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "alembic" / "versions" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Sqlite:
    """One SQLite file behind ``db_compat`` / ``db_pool``: the notes, card, registry and app_settings tables, with a per-table
    DELETE / UPDATE failure switch (``fail[table] = n``: the next n writes to it raise)."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.fail: dict = {}

    def execute(self, sql, params=()):
        async def _run():
            await asyncio.sleep(0)
            head = " ".join(sql.split()[:3]).upper()
            for table, n in list(self.fail.items()):
                if n > 0 and head.startswith(("DELETE FROM " + table.upper(), "UPDATE " + table.upper())):
                    self.fail[table] = n - 1
                    raise Injected(f"injected {table} write failure")
            cur = self.conn.execute(sql, tuple(params))
            return _Cursor(cur.fetchall(), rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()

    def q(self, sql, params=()):
        return [tuple(r) for r in self.conn.execute(sql, params).fetchall()]


class _Offers:
    """``pending_suggestions`` over asyncpg-shaped calls (``fetch`` / ``execute`` with ``$n``)."""

    def __init__(self):
        self.rows = {1: {"id": 1, "action_type": "reminder", "description": "Remind you to call your plumber?", "offer_phrase": "",
                         "pre_filled_slots": "{}"}}
        self.fail = 0

    def ctx(self):
        offers = self

        class _Db:
            async def fetch(self, sql, *args):
                return [dict(r) for r in offers.rows.values()]

            async def execute(self, sql, *args):
                if sql.lstrip().upper().startswith("DELETE"):
                    if offers.fail > 0:
                        offers.fail -= 1
                        raise Injected("injected offers failure")
                    offers.rows.pop(int(args[0]) if str(args[0]).isdigit() else args[0], None)
                return "DELETE 1"

        @contextlib.asynccontextmanager
        async def _ctx():
            yield _Db()
        return _ctx()


@pytest.fixture
def w(world, monkeypatch, tmp_path):
    svc, chat = world
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "1")
    db = _Sqlite(str(tmp_path / "zoe.db"))
    for sql in _mig("0080_memory_value_registry").statements():
        db.conn.execute(sql)
    notes = _mig("0074_conversation_notes")
    db.conn.execute(notes.NOTES)
    db.conn.execute("CREATE TABLE user_model_cards (user_id TEXT PRIMARY KEY, card_json TEXT NOT NULL, card_text TEXT NOT NULL, "
                    "version TEXT NOT NULL, built_at TEXT NOT NULL, source_count INTEGER NOT NULL DEFAULT 0)")
    db.conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.conn.execute("CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, deleted INTEGER DEFAULT 0)")
    db.conn.commit()

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db
    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setattr(db_pool, "get_db_ctx", fake_db)
    offers = _Offers()
    monkeypatch.setattr(pending_suggestions, "get_db_ctx", offers.ctx)
    user_erase_gate.reset()
    if forget_record is not None:
        forget_record.set_backend(forget_record.SqlBackend())        # the REAL statements, on SQLite

    erased: list = []

    async def erase_rows(user_id, ids, *, actor, reason="", gate_held=False, lock_held=False):
        # MemoryService.erase_rows' order: the registry entries tied to the rows FIRST (fail closed), then the rows themselves
        import memory_value_registry

        ids = [str(i) for i in ids]
        await memory_value_registry.erase_for_rows(user_id, ids)
        if erase_rows.fail > 0:
            erase_rows.fail -= 1
            raise Injected("injected physical erase failure")
        for i in ids:
            svc._collection().rows.pop(i, None)
            erased.append(i)
        return {"rows_removed": len(ids)}
    erase_rows.fail = 0
    svc.erase_rows = erase_rows
    review = svc.review

    async def failing_review(mem_id, **kw):
        if kw.get("decision") in ("reject", "archive") and failing_review.fail > 0:
            failing_review.fail -= 1
            raise Injected("injected reject failure")
        return await review(mem_id, **kw)
    failing_review.fail = 0
    svc.review = failing_review

    rows = _plant(svc, chat, "a")
    fact, copy = rows["a"]
    mid = chat.mid("a")
    nb = night_store.get_backend()
    nb.obs_rows["o-a"]["thread_id"] = "t-a"
    run(nb.put_observation(night_store.obs_row(id="o-z", user_id=UID, turn_id=OTHER_MID, quote="the garden gate squeaks loudly",
                                               day="2026-10-08", said_at=time.time(), thread_id="t-a")))
    run(nb.put_thread(night_store.thread_row(id="t-a", user_id=UID, title="plumber and the gate", anchors="plumber gate",
                                             topic="plumber", mentions_n=2, first_day="2026-10-08", last_day="2026-10-08")))
    now = "2026-10-08T10:00:00Z"
    for nid, tids, summary in (("note-a", [mid], "Talked about the plumber works"), ("note-z", [OTHER_MID], "Talked about the gate")):
        db.conn.execute("INSERT INTO conversation_notes (id, user_id, episode_key, source, started_at, ended_at, turn_ids, summary, "
                        "created_at) VALUES (?, ?, ?, 'chat', ?, ?, ?, ?, ?)", (nid, UID, nid, now, now, json.dumps(tids), summary, now))
    db.conn.execute("INSERT INTO user_model_cards VALUES (?, ?, 'card', 'v1', ?, 2)",
                    (UID, json.dumps({"items": [["fact", "x", fact.id], ["fact", "y", "row-unrelated"]]}), now))
    # reg-z: recorded from ANOTHER turn (a topic forget over-forgets an entry with no source turn, by design)
    for rid, tied, turn in (("reg-a", [fact.id], None), ("reg-z", ["row-unrelated"], "ut-control-turn")):
        db.conn.execute("INSERT INTO memory_value_registry (id, user_id, subject_ref, attribute, role, value_norm, said_at, by_claim, "
                        "source_turn_id, row_ids, expires_at, created_at) VALUES (?, ?, 'self', 'age', 'asserted', '{}', 1, 'c', ?, ?, "
                        "9999999999, 1)", (rid, UID, turn, json.dumps(tied)))
    db.conn.commit()
    return type("W", (), dict(svc=svc, chat=chat, db=db, offers=offers, fact=fact, copy=copy, mid=mid, erased=erased,
                              review=failing_review, erase=erase_rows, rows=rows))


# ── the entry points ─────────────────────────────────────────────────────────────────────────────────────────────────

def _explained_row(w, first):
    exp = (mp._state(UID).explained if not first and mp._state(UID).explained is not None else None) or mp.Explained(
        seq=1, row_id=w.fact.id, ts=time.time(), text=w.fact.text, quote=str(w.fact.metadata.get("source_excerpt") or ""))
    return run(pa.forget_it(UID, exp, svc=w.svc)).startswith("Done")


def _explained_turn(w, first):
    if first:
        tid = _xw_rows()["a"]
        stored = xw.get_backend().rows[(UID, tid)]["text"]
        w.exp = mp.Explained(seq=1, row_id="", ts=time.time(), text=stored, quote=stored, turn_id=tid)
    exp = (mp._state(UID).explained if not first else None) or w.exp
    return run(pa._forget_turn(UID, exp, svc=w.svc)).startswith("Done")


def _forget_last(w, first):
    return run(intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID)).startswith("Done")


def _raises_or(fn):
    try:
        return run(fn()) == "forgotten"
    except Exception:  # noqa: BLE001 - the route turns a raise into "not forgotten, try again"
        return False


def _memory_space_row(w, first):
    return _raises_or(lambda: memory_space.forget_row(UID, w.fact.id, svc=w.svc))


def _guardian_row(w, first):
    return _raises_or(lambda: memory_space.guardian_forget(UID, w.fact.id, actor=ADMIN, svc=w.svc))


def _topic(w, first):
    res = run(forget_topic.forget(w.svc, UID, "my plumber"))
    return bool(res.forgotten) and not (res.store_down or res.failed or res.too_many)


DRIVERS = {"forget_last": _forget_last, "explained_row": _explained_row, "explained_turn": _explained_turn,
           "memory_space_row": _memory_space_row, "guardian_row": _guardian_row, "topic": _topic}


# ── the step seams: each fails the STORE the step writes (a seam that exists on the old and the new code) ──────────────────

class _Counted:
    def __init__(self, obj, name):
        self.obj, self.name, self.orig, self.fail = obj, name, getattr(obj, name), 0

    def install(self, n):
        self.fail = n
        orig, me = self.orig, self

        async def failing(*a, **k):
            if me.fail > 0:
                me.fail -= 1
                raise Injected(f"injected {me.name} failure")
            return await orig(*a, **k)
        setattr(self.obj, self.name, failing)


def _seam(w, step, n):
    """Arm the failure; returns a function telling how many injected failures are still unfired."""
    if step == "reject":
        w.review.fail = n
        return lambda: w.review.fail
    if step == "erase_rows":
        w.erase.fail = n
        return lambda: w.erase.fail
    if step == "transcript":
        c = _Counted(xw, "_set_transcript_metadata")
    elif step == "exact_words":
        c = _Counted(xw.get_backend(), "delete")
    elif step == "night_mind":
        c = _Counted(night_store.get_backend(), "erase_matching")
    elif step in ("notes", "card", "registry"):
        table = {"notes": "conversation_notes", "card": "user_model_cards", "registry": "memory_value_registry"}[step]
        w.db.fail[table] = n
        return lambda: w.db.fail.get(table, 0)
    elif step == "offers":
        w.offers.fail = n
        return lambda: w.offers.fail
    else:
        raise AssertionError(f"no failure seam for step {step!r}: add one (the step list is read from forget_record.STEPS)")
    c.install(n)
    return lambda: c.fail


def _left(w, code) -> dict:
    """What of the target turn is still anywhere (True = still there: a bug once the forget is confirmed)."""
    ap = _approved(w.svc)
    threads = run(night_store.get_backend().threads(UID))
    out = {
        "rows_approved": bool({FACTS["a"], COPIES["a"]} & ap),
        "rows_on_disk": not {w.fact.id, w.copy.id} <= set(w.erased),
        "transcript": not w.chat.off(w.mid),
        "exact_words": "a" in _xw_rows(),
        "observation": "o-a" in {o["id"] for o in run(night_store.get_backend().observations(UID))},
        "thread": any("plumber" in json.dumps(t).lower() for t in threads),
        "note": bool(w.db.q("SELECT id FROM conversation_notes WHERE id = 'note-a'")),
        "card": bool(w.db.q("SELECT user_id FROM user_model_cards WHERE user_id = ?", (UID,))),
        "registry": bool(w.db.q("SELECT id FROM memory_value_registry WHERE id = 'reg-a'")),
    }
    if code == "topic":
        out["offer"] = 1 in w.offers.rows
    return out


def _control_kept(w) -> None:
    assert FACTS["c"] in _approved(w.svc), "another turn's fact was forgotten too"
    assert w.db.q("SELECT id FROM conversation_notes WHERE id = 'note-z'"), "another turn's note was erased"
    assert w.db.q("SELECT id FROM memory_value_registry WHERE id = 'reg-z'"), "an unrelated registry entry was erased"
    assert "o-z" in {o["id"] for o in run(night_store.get_backend().observations(UID))}, "another turn's observation was erased"


@pytest.mark.parametrize("times", [1, 2], ids=["once", "twice"])
@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("code", sorted(DRIVERS))
def test_a_failed_step_is_never_confirmed_and_the_retry_finishes_everything(w, code, step, times):
    drive = DRIVERS[code]
    unfired = _seam(w, step, times)
    first = drive(w, True)
    fired = unfired() < times
    if fired:
        assert not first, f"{code}: confirmed although the {step} step failed"
    if forget_record is not None and not first:      # the open record a retry finishes from holds ids and codes, never the words
        for rec in run(forget_record.get_backend().open_records(UID)):
            assert not {"plumber", "example", "street", "works"} & set(rec.raw.lower().replace('"', " ").split()), rec.raw
    replies = [first]
    while not replies[-1] and len(replies) < 4:
        replies.append(drive(w, False))
    left = {k: v for k, v in _left(w, code).items() if v}
    assert replies[-1], f"{code}/{step}x{times}: never confirmed after {len(replies)} tries; still there: {sorted(left)}"
    assert not left, f"{code}/{step}x{times}: confirmed with {sorted(left)} left behind (replies {replies})"
    _control_kept(w)
    if forget_record is not None:
        assert not run(forget_record.get_backend().open_records(UID)), "a confirmed forget left its record open"


# ── the matrix is the code's: steps and entry points are enumerated from forget_record, not written here ──────────────────

@pytest.mark.skipif(forget_record is None, reason="the old code has no record")
def test_every_step_has_a_failure_seam():
    w = type("W", (), {"review": type("R", (), {"fail": 0})(), "erase": type("E", (), {"fail": 0})(),
                       "db": type("D", (), {"fail": {}})(), "offers": type("O", (), {"fail": 0})()})
    for step in forget_record.STEPS:
        _seam(w, step, 0)          # raises for a step this file cannot fail


def _record_call_codes() -> dict:
    """``{code: [file:line]}`` of every ``forget_record.forget(..., code=<literal>)`` in services/zoe-data (not tests)."""
    out: dict = {}
    for p in sorted(ROOT.rglob("*.py")):
        if "tests" in p.parts or "alembic" in p.parts or p.name == "forget_record.py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "forget" \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "forget_record":
                code = next((k.value.value for k in node.keywords if k.arg == "code" and isinstance(k.value, ast.Constant)), None)
                assert code is not None, f"{p.name}:{node.lineno}: forget_record.forget needs a literal code="
                out.setdefault(code, []).append(f"{p.name}:{node.lineno}")
    return out


@pytest.mark.skipif(forget_record is None, reason="the old code has no record")
def test_every_entry_point_is_driven_here_and_declared_in_the_code():
    calls = _record_call_codes()
    assert set(calls) == set(forget_record.ENTRY_POINTS), f"call sites {sorted(calls)} vs ENTRY_POINTS {forget_record.ENTRY_POINTS}"
    assert set(DRIVERS) == set(forget_record.ENTRY_POINTS), "an entry point has no driver in this matrix: add one"
