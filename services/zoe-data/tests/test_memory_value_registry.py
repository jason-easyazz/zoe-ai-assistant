"""The superseded-value registry (alembic 0080, ``memory_value_registry.py``): the migration, the said-at rule, every forget path
that must erase it, the shadow wiring (no behaviour change with the flag off) and the no-words guarantee.

SQLite with the REAL migration 0080 and a minimal ``people`` table behind ``db_compat.get_compat_db``. Invented names only (the repo
is public).

Negative controls: each forget path has a call-site check that is run on the real source AND on the same source with that one hook
removed (it must go red), and each behaviour test was also run by hand with its hook removed (recorded in the PR body).
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import importlib.util
import json
import logging
import re
import sqlite3
import time
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import db_compat
import memory_value_registry as reg
import user_erase_gate
from db_pool import _Cursor, _ExecResult

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
MIG = SVC / "alembic/versions/0080_memory_value_registry.py"
A, B = "demo_mvr_member_a", "demo_mvr_member_b"


def U(tag: str) -> str:
    """A people.id as every people writer mints it (uuid4)."""
    return str(uuid.UUID(hashlib.md5(tag.encode()).hexdigest(), version=4))


def R(user: str, tag: str) -> str:
    """A memory row id as memory_service._memory_id mints it: zoe_<member>_<sha256[:24]>."""
    return f"zoe_{user}_{hashlib.sha256(tag.encode()).hexdigest()[:24]}"


def T(tag: str) -> str:
    """A turn content id (people_graph.content_turn_id's shape)."""
    return "ut-" + hashlib.sha1(tag.encode()).hexdigest()[:16]


def C(tag) -> str:
    """A claim id (memory_claims.claim_id's shape)."""
    return hashlib.sha256(str(tag).encode()).hexdigest()[:16]


JUNO, WREN, OTHER_JUNO = U("juno"), U("wren"), U("juno-b")
T0 = float(int(time.time()))
NAMES = ("Juno", "Wren")
PEOPLE = ("CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT, deleted INTEGER DEFAULT 0, "
          "updated_at TEXT)")
COLUMNS = {"id", "user_id", "subject_ref", "attribute", "role", "value_norm", "said_at", "by_claim", "source_turn_id", "row_ids",
           "expires_at", "created_at"}


def run(coro):
    return asyncio.run(coro)


def _mod():
    spec = importlib.util.spec_from_file_location("mig_0080", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _migrate(engine, fn="upgrade"):
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(_mod(), fn)()


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)

    def execute(self, sql, params=()):
        async def _run():
            await asyncio.sleep(0)
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()

    def q(self, sql, params=()):
        return [tuple(r) for r in self.conn.execute(sql, params).fetchall()]


@pytest.fixture
def db(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    d = _Sqlite(path)
    d.conn.executescript(PEOPLE + ";")
    d.conn.executemany("INSERT INTO people (id, user_id, name, deleted) VALUES (?, ?, ?, 0)",
                       [(JUNO, A, "Juno"), (WREN, A, "Wren"), (OTHER_JUNO, B, "Juno")])
    d.conn.commit()
    _migrate(sa.create_engine(f"sqlite:///{path}"))

    @contextlib.asynccontextmanager
    async def fake_db():
        yield d

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.delenv(reg.FLAG, raising=False)
    user_erase_gate.reset()
    reg.reset_state()
    yield d
    user_erase_gate.reset()


def _age(x):
    return {"v": float(x)}


def _rec(user, subject, old, new, at, *, attribute="age", turn=None, rows=(), claim=None):
    return run(reg.record(user, subject, attribute, superseded=[old] if old is not None else [], asserted=new, said_at=at,
                          by_claim=claim or C(int(at)), source_turn_id=turn, row_ids=rows, now=at))


def _seed(db):
    """Member A: Juno 7 -> 8 (turn t1, rows r1/r1n), Wren 3 -> 4 (turn t2, rows r2/r2n). Member B: their own Juno."""
    assert _rec(A, JUNO, _age(7), _age(8), T0, turn=T("aaaa"), rows=[R(A, "r1"), R(A, "r1n")]) == 2
    assert _rec(A, WREN, _age(3), _age(4), T0 + 1, turn=T("bbbb"), rows=[R(A, "r2"), R(A, "r2n")]) == 2
    assert _rec(B, OTHER_JUNO, _age(5), _age(6), T0 + 2, turn=T("cccc"), rows=[R(B, "r9")]) == 2
    return db


def _subjects(db, user):
    return sorted({r[0] for r in db.q("SELECT subject_ref FROM memory_value_registry WHERE user_id = ?", (user,))})


# ── 1. the migration ────────────────────────────────────────────────────────────────────────

def test_revision_chains_onto_the_head_of_main_and_there_is_one_head():
    mod = _mod()
    assert mod.revision == "0080" and mod.down_revision == "0079"     # on the calendar feed (PR #2474): this PR depends on it
    revs, downs = set(), set()
    for f in (SVC / "alembic/versions").glob("*.py"):
        src = f.read_text()
        r = re.search(r'^revision = "(\w+)"', src, re.M)
        d = re.search(r'^down_revision = "(\w+)"', src, re.M)
        if r:
            revs.add(r.group(1))
        if d:
            downs.add(d.group(1))
    assert "0079" in revs, "0080 chains onto 0079 (PR #2474, calendar feed): rebase onto main once it has landed"
    assert mod.down_revision in revs
    assert revs - downs == {"0080"}, f"expected the single head 0080, got {sorted(revs - downs)}"


def test_sqlite_upgrade_twice_then_downgrade_twice(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT, name TEXT)")
        conn.exec_driver_sql("INSERT INTO people VALUES ('p1', 'u1', 'Juno')")
    _migrate(engine)
    _migrate(engine)                                    # rerun: a no-op
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(memory_value_registry)")}
        idx = {r[1]: r for r in conn.exec_driver_sql("PRAGMA index_list(memory_value_registry)")}
        key = [r[2] for r in conn.exec_driver_sql("PRAGMA index_info(idx_memory_value_registry_key)")]
    assert cols == COLUMNS and "idx_memory_value_registry_key" in idx
    assert key == ["user_id", "subject_ref", "attribute", "said_at"]
    _migrate(engine, "downgrade")
    _migrate(engine, "downgrade")                       # already gone: a no-op
    with engine.connect() as conn:
        assert not conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE name LIKE '%memory_value_registry%'").fetchall()
        assert conn.exec_driver_sql("SELECT name FROM people").fetchall() == [("Juno",)]      # no other table touched


def test_the_check_constraints_hold_the_closed_vocabulary(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO memory_value_registry (id, user_id, subject_ref, attribute, role, value_norm, said_at, by_claim, "
                        "expires_at, created_at) VALUES ('x', 'u', 'self', 'location', 'superseded', '{}', 1, 'c', 2, 1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO memory_value_registry (id, user_id, subject_ref, attribute, role, value_norm, said_at, by_claim, "
                        "expires_at, created_at) VALUES ('y', 'u', 'self', 'age', 'mentioned', '{}', 1, 'c', 2, 1)")


def test_the_module_and_the_migration_share_one_vocabulary():
    mod = _mod()
    assert tuple(mod.ATTRIBUTES) == reg.ATTRIBUTES and tuple(mod.ROLES) == reg.ROLES and mod.TABLE == reg.TABLE


def test_no_request_time_ddl():
    src = (SVC / "memory_value_registry.py").read_text()
    assert "CREATE TABLE" not in src and "ALTER TABLE" not in src and "DROP TABLE" not in src


# ── 2. normalised values: numbers and codes, never words ────────────────────────────────────

@pytest.mark.parametrize("attribute, value, want", [
    ("age", {"v": 8}, '{"v":8.0}'),
    ("age", {"v": 7.5}, '{"v":7.5}'),
    ("event_date", {"m": 12, "d": 19}, '{"d":19,"m":12}'),
    ("birthday", {"m": 5, "d": 3, "y": 2019}, '{"d":3,"m":5,"y":2019}'),
    ("amount", {"v": "13500", "cur": "AUD"}, '{"cur":"AUD","v":"13500"}'),
    ("amount", {"v": "13.5", "cur": "USD"}, '{"cur":"USD","v":"13.5"}'),
    ("measure", {"v": "5", "u": "km"}, '{"u":"km","v":"5"}'),
    ("count", {"v": 3}, '{"v":3}'),
    ("time_of_day", {"h": 17, "mi": 0}, '{"h":17,"mi":0}'),
    ("schedule", {"freq": "weekly", "byday": ["WE"], "time": {"h": 16, "mi": 0}},
     '{"byday":["WE"],"freq":"weekly","time":{"h":16,"mi":0}}'),
])
def test_normalise_is_canonical(attribute, value, want):
    assert reg.normalise(attribute, value) == want


@pytest.mark.parametrize("attribute, value", [
    ("age", {"v": "eight"}), ("age", {"v": 7.3}), ("age", {"v": 130}), ("age", {"v": True}), ("age", 8),
    ("age", {"v": 8, "who": "Juno"}), ("amount", {"v": "100", "cur": "Juno"}), ("amount", {"v": "twelve", "cur": "AUD"}),
    ("amount", {"v": "13.50", "cur": "AUD"}), ("amount", {"v": 13500, "cur": "AUD"}), ("time_of_day", {"h12": 5, "mi": 0}),
    ("measure", {"v": "5", "u": "Ballarat"}), ("count", {"n": 3}), ("schedule", {"freq": "weekly", "byday": ["swimming"]}), ("birthday", {"m": 5}),
    ("event_date", {"m": 13, "d": 1}), ("event_date", {}), ("location", {"v": "Ballarat"}), ("employer", {"v": "x"}),
    ("diet", {"v": "vegan"}), ("count", {"v": float("nan")}),
])
def test_normalise_refuses_anything_that_could_carry_words(attribute, value):
    assert reg.normalise(attribute, value) is None


@pytest.mark.parametrize("cur, ok", [("AUD", True), ("USD", True), ("EUR", True), ("GBP", True), ("NZD", True),
                                     ("ZOE", False), ("AMY", False), ("JOE", False), ("XYZ", False), ("aud", False), ("AU", False)])
def test_a_currency_is_a_closed_code_set_never_three_letters(cur, ok):
    """Three-letter NAMES are real (AMY, JOE, ZOE): a shape check would store one in value_norm."""
    assert (reg.normalise("amount", {"v": "12", "cur": cur}) is not None) is ok


def test_every_value_norm_string_is_from_a_closed_set():
    assert reg.CURRENCIES == frozenset({"AUD", "EUR", "GBP", "NZD", "USD"})
    for bad in ({"v": "5", "u": "Zoe"}, {"v": "5", "u": "kms"}, {"v": "5", "u": ["km"]}):
        assert reg.normalise("measure", bad) is None
    for bad in ({"freq": "Juno"}, {"freq": "weekly", "byday": ["ZOE"]}, {"freq": "weekly", "byday": ["Mo"]}, {"freq": ["weekly"]}):
        assert reg.normalise("schedule", bad) is None


def test_a_name_shaped_currency_refuses_the_whole_record(db):
    assert run(reg.record(A, "self", "amount", superseded=[{"v": "12000", "cur": "AUD"}], asserted={"v": "13500", "cur": "ZOE"},
                          said_at=T0, by_claim=C(1), now=T0)) == 0
    assert db.q("SELECT COUNT(*) FROM memory_value_registry") == [(0,)]
    assert run(reg.record(A, "self", "amount", superseded=[{"v": "12000", "cur": "AUD"}], asserted={"v": "13500", "cur": "AUD"},
                          said_at=T0, by_claim=C(1), now=T0)) == 2                                        # control


# ── 3. record + is_superseded: the said-at rule ─────────────────────────────────────────────

def test_only_corrections_said_after_the_fact_count(db):
    _rec(A, JUNO, _age(7), _age(8), T0)
    assert run(reg.is_superseded(A, JUNO, "age", _age(7), T0 - 60, now=T0)) is True       # said before the correction: stale
    assert run(reg.is_superseded(A, JUNO, "age", _age(7), T0 + 60, now=T0)) is False      # said AFTER it: the newer word, kept
    assert run(reg.is_superseded(A, JUNO, "age", _age(8), T0 - 60, now=T0)) is False      # the corrected-to value is never stale
    assert run(reg.is_superseded(A, WREN, "age", _age(7), T0 - 60, now=T0)) is False      # another subject
    assert run(reg.is_superseded(B, JUNO, "age", _age(7), T0 - 60, now=T0)) is False      # another member


def test_a_to_b_to_a_keeps_the_latest(db):
    """7 -> 8, then "actually 7 after all": 7 is current again, 8 is the stale one (design note C04)."""
    _rec(A, JUNO, _age(7), _age(8), T0)
    _rec(A, JUNO, _age(8), _age(7), T0 + 100)
    assert run(reg.is_superseded(A, JUNO, "age", _age(7), T0 - 10, now=T0)) is False
    assert run(reg.is_superseded(A, JUNO, "age", _age(8), T0 + 50, now=T0)) is True
    assert run(reg.is_superseded(A, JUNO, "age", _age(8), T0 - 10, now=T0)) is True
    assert run(reg.is_superseded(A, JUNO, "age", _age(7), T0 + 150, now=T0)) is False


def test_an_amount_chain_back_and_forth(db):
    """12k -> 13.5k, days later 12k again (design note C17); an amount is not single-valued."""
    k12, k135 = {"v": "12000", "cur": "AUD"}, {"v": "13500", "cur": "AUD"}
    _rec(A, "self", k12, k135, T0, attribute="amount")
    _rec(A, "self", k135, k12, T0 + 3 * 86400, attribute="amount")
    now = T0 + 3 * 86400
    assert run(reg.is_superseded(A, "self", "amount", k12, T0 - 10, now=now)) is False      # re-asserted after the correction
    assert run(reg.is_superseded(A, "self", "amount", k135, T0 + 10, now=now)) is True
    assert run(reg.is_superseded(A, "self", "amount", k12, T0 + 4 * 86400, now=now)) is False


def test_a_restatement_with_nothing_stored_makes_another_age_stale(db):
    """"juno just turned 8" with no old value: for a single-valued attribute any older other value is stale."""
    _rec(A, JUNO, None, _age(8), T0)
    assert run(reg.is_superseded(A, JUNO, "age", _age(7), T0 - 10, now=T0)) is True
    assert run(reg.is_superseded(A, JUNO, "age", _age(8), T0 - 10, now=T0)) is False


def test_an_event_date_is_not_single_valued(db):
    _rec(A, JUNO, None, {"m": 12, "d": 19}, T0, attribute="event_date")
    assert run(reg.is_superseded(A, JUNO, "event_date", {"m": 12, "d": 5}, T0 - 10, now=T0)) is False


def test_entries_expire_after_seven_days(db):
    _rec(A, JUNO, _age(7), _age(8), T0)
    assert run(reg.is_superseded(A, JUNO, "age", _age(7), T0 - 10, now=T0 + reg.RETENTION_S - 1)) is True
    assert run(reg.is_superseded(A, JUNO, "age", _age(7), T0 - 10, now=T0 + reg.RETENTION_S + 1)) is False
    _rec(A, WREN, _age(3), _age(4), T0 + reg.RETENTION_S + 5)        # the next record sweeps the expired rows
    assert _subjects(db, A) == [WREN]


@pytest.mark.parametrize("kwargs", [
    {"subject": "Juno"}, {"subject": "juno"}, {"subject": "daughter"}, {"subject": "Juno Hale"},   # a NAME is never a person id
    {"subject": U("nobody")},                # a well-shaped id of nobody (no people row)
    {"subject": OTHER_JUNO},                 # another member's person
    {"old": {"v": "seven"}},
    {"turn": "Juno"}, {"turn": "daughter"}, {"turn": "secret_note"}, {"turn": "my daughter juno is 8"},
    {"turn": "ut-juno"}, {"turn": "ut-" + "a" * 15},
    {"rows": ["Juno"]}, {"rows": ["daughter"]}, {"rows": ["secret_note"]}, {"rows": ["a row text with spaces"]},
    {"rows": [R(B, "r1")]},                  # another member's row id
    {"rows": ["zoe_" + A + "_juno"]},
    {"claim": "Juno"}, {"claim": "daughter"}, {"claim": "secret_note"}, {"claim": "juno is 8 not 7"}, {"claim": "vc-juno"},
    {"user": "Juno Hale"}, {"user": "guest-1234"}, {"user": "guest"}, {"user": ""},
])
def test_record_refuses_names_words_and_unknown_subjects(db, kwargs):
    assert run(reg.record(kwargs.get("user", A), kwargs.get("subject", JUNO), "age", superseded=[kwargs.get("old", _age(7))],
                          asserted=_age(8), said_at=T0, by_claim=kwargs.get("claim", C(1)), source_turn_id=kwargs.get("turn", T("1")),
                          row_ids=kwargs.get("rows", [R(A, "r1")]), now=T0)) == 0
    assert db.q("SELECT COUNT(*) FROM memory_value_registry") == [(0,)]


def test_a_name_shaped_people_id_is_refused_even_when_that_row_exists(db):
    """The subject check is the id SHAPE, not only "a people row exists": a legacy / hand-made row whose id is a name stays out."""
    db.conn.execute("INSERT INTO people (id, user_id, name, deleted) VALUES ('juno', ?, 'Juno', 0)", (A,))
    db.conn.commit()
    assert run(reg.record(A, "juno", "age", superseded=[_age(7)], asserted=_age(8), said_at=T0, by_claim=C(1), now=T0)) == 0
    assert db.q("SELECT COUNT(*) FROM memory_value_registry") == [(0,)]


def test_the_well_shaped_ids_are_accepted(db):
    """Control for the refusals above: the same call with every id in its real shape writes."""
    assert run(reg.record(A, JUNO, "age", superseded=[_age(7)], asserted=_age(8), said_at=T0, by_claim=C(1), source_turn_id=T("1"),
                          row_ids=[R(A, "r1")], now=T0)) == 2
    assert run(reg.record(A, JUNO, "age", superseded=[_age(8)], asserted=_age(9), said_at=T0 + 1, by_claim="vc-" + C(2),
                          now=T0 + 1)) == 2


def test_a_member_erase_in_flight_drops_the_record(db):
    gen = user_erase_gate.generation(A)

    async def go():
        async with user_erase_gate.erasing(A):
            pass
        return await reg.record(A, JUNO, "age", superseded=[_age(7)], asserted=_age(8), said_at=T0, by_claim=C(1),
                                scheduled_at=gen, now=T0)
    assert run(go()) == 0
    assert db.q("SELECT COUNT(*) FROM memory_value_registry") == [(0,)]


def test_a_subject_forgotten_while_the_record_writes_takes_the_record_with_it(db, monkeypatch):
    seen = {"n": 0}
    real = reg._person_live

    async def flips(conn, uid, pid):
        seen["n"] += 1
        if seen["n"] == 2:                    # the post-commit re-check: an entity forget soft-deleted the person meanwhile
            db.conn.execute("UPDATE people SET deleted = 1 WHERE id = ?", (pid,))
        return await real(conn, uid, pid)
    monkeypatch.setattr(reg, "_person_live", flips)
    assert _rec(A, JUNO, _age(7), _age(8), T0) == 0
    assert db.q("SELECT COUNT(*) FROM memory_value_registry") == [(0,)]


# ── 4. forget reach: every path, each with its control ─────────────────────────────────────

SAID = "juno is 8 not 7"


def _erasers():
    from people_graph import content_turn_id

    return {
        "topic": lambda: reg.erase_topic(A, row_ids=[R(A, "r7")]),
        "turn": lambda: reg.erase_for_turn(A, SAID),
        "rows": lambda: reg.erase_for_rows(A, [R(A, "r7")]),
    }, content_turn_id(A, SAID)


async def _naive_erase(db):
    """The race the gate closes: read, select and delete with no gate and no epoch (what ``_erase_where`` did before)."""
    rows = await reg._fetch(db, "SELECT id, row_ids FROM memory_value_registry WHERE user_id = ?", (A,))
    await asyncio.sleep(0.01)
    for r in rows:
        if R(A, "r7") in json.loads(r[1]):
            db.conn.execute("DELETE FROM memory_value_registry WHERE id = ?", (r[0],))
    db.conn.commit()


def _race(db, monkeypatch, eraser, *, before_insert=True):
    """A record pauses INSIDE its write (holding the gate, after its pre-insert checks) while ``eraser`` starts; returns the
    record's result and the entries left that the erase should have reached."""
    _er, turn = _erasers()

    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        real = reg._person_live
        calls = {"n": 0}

        async def paused(conn, uid, pid):
            calls["n"] += 1
            if calls["n"] == 1:
                entered.set()
                await release.wait()
            return await real(conn, uid, pid)
        monkeypatch.setattr(reg, "_person_live", paused)
        rec = asyncio.create_task(reg.record(A, JUNO, "age", superseded=[_age(7)], asserted=_age(8), said_at=T0, by_claim=C(7),
                                             source_turn_id=turn, row_ids=[R(A, "r7")], now=T0))
        await entered.wait()
        er = asyncio.create_task(eraser())
        await asyncio.sleep(0.02)            # the eraser has started: queued on the gate (or, ungated, already read)
        release.set()
        out = await rec
        await er
        return out
    out = run(go())
    return out, db.q("SELECT COUNT(*) FROM memory_value_registry WHERE user_id = ?", (A,))[0][0]


def test_the_race_is_real_without_the_gate(db, monkeypatch):
    """Harness control: an ungated read-select-delete erase that starts while a record is writing leaves the record's entries."""
    out, left = _race(db, monkeypatch, lambda: _naive_erase(db))
    assert out == 2 and left == 2


@pytest.mark.parametrize("path", ["topic", "turn", "rows"])
def test_an_erase_that_starts_while_a_record_writes_leaves_nothing(db, monkeypatch, path):
    erasers, _turn = _erasers()
    out, left = _race(db, monkeypatch, erasers[path])
    assert left == 0, f"{path}: an entry survived a completed forget"
    assert out == 0                               # the record took itself back (the epoch moved under it)


@pytest.mark.parametrize("path", ["topic", "turn", "rows"])
def test_a_record_scheduled_before_an_erase_never_lands_after_it(db, path):
    erasers, turn = _erasers()
    part, gen = reg.partial_epoch(A), user_erase_gate.generation(A)       # the record is SCHEDULED here...
    run(erasers[path]())                                                    # ...the erase runs to completion...
    assert run(reg.record(A, JUNO, "age", superseded=[_age(7)], asserted=_age(8), said_at=T0, by_claim=C(7), source_turn_id=turn,
                          row_ids=[R(A, "r7")], partial_at=part, scheduled_at=gen, now=T0)) == 0     # ...and the late write is dropped
    assert db.q("SELECT COUNT(*) FROM memory_value_registry") == [(0,)]


def test_the_scheduled_before_control_writes_when_no_erase_ran(db):
    part, gen = reg.partial_epoch(A), user_erase_gate.generation(A)
    assert run(reg.record(A, JUNO, "age", superseded=[_age(7)], asserted=_age(8), said_at=T0, by_claim=C(7), row_ids=[R(A, "r7")],
                          partial_at=part, scheduled_at=gen, now=T0)) == 2


def test_an_entity_erase_on_the_cascade_connection_also_drops_a_record_in_flight(db, monkeypatch):
    out, left = _race(db, monkeypatch, lambda: reg.erase_entity(A, re.compile(r"\bzzz\b"), db=db))
    assert out == 0 and left == 0                 # no name matched, yet the in-flight record took itself back (over-forget is safe)


def test_member_erase_removes_every_row_of_the_member_only(db):
    _seed(db)
    assert run(reg.forget_user(A)) == 4
    assert _subjects(db, A) == [] and _subjects(db, B) == [OTHER_JUNO]


def test_member_erase_fails_closed_on_a_lost_store(monkeypatch):
    @contextlib.asynccontextmanager
    async def lost():
        raise ConnectionError("pool lost")
        yield  # pragma: no cover
    monkeypatch.setattr(db_compat, "get_compat_db", lost)
    with pytest.raises(ConnectionError):
        run(reg.forget_user(A))


def test_entity_forget_through_the_real_cascade_removes_that_person_only(db, monkeypatch):
    """The cascade's DATABASE pass (``_run``: the people step, then this one), on the test DB. ``cascade_forget`` as a whole is not
    run here: its file steps (the local text logs, self-review, night extract) would read the box's own files."""
    import memory_forget_cascade as cascade

    monkeypatch.setattr(cascade, "RETRY_DELAY_S", 0)
    _seed(db)
    res = cascade.CascadeResult()
    run(cascade._run(db, A, cascade._name_re("Juno"), res))
    assert "value_registry" not in res.failed and "people" not in res.failed
    assert _subjects(db, A) == [WREN]                                    # Juno's entries gone, Wren's kept
    assert _subjects(db, B) == [OTHER_JUNO]                              # another member's Juno is not this member's forget
    assert db.q("SELECT deleted FROM people WHERE id = ?", (JUNO,)) == [(1,)]


def test_entity_forget_reaches_a_person_soft_deleted_by_an_earlier_pass(db):
    _seed(db)
    db.conn.execute("UPDATE people SET deleted = 1 WHERE id = ?", (JUNO,))
    assert run(reg.erase_entity(A, re.compile(r"\bjuno\b", re.I), db=db)) == 2
    assert _subjects(db, A) == [WREN]


def test_the_cascade_step_is_retried_from_the_ledger(db):
    import memory_forget_cascade as cascade

    assert "value_registry" in cascade.DB_STEPS and "value_registry" in cascade.ALL_LABELS


def _registry_svc(*rows):
    class _Row:
        def __init__(self, i, text):
            self.id, self.text, self.metadata = i, text, {"user_id": A}

    class _Svc:
        def __init__(self):
            self.rows = [_Row(i, t) for i, t in rows]

        async def list_by_status(self, **_k):
            return list(self.rows)

        async def search(self, *_a, **_k):
            return list(self.rows)

        async def review(self, *_a, **_k):
            return object()
    return _Svc()


@pytest.fixture
def forget_env(monkeypatch):
    import exact_words
    import memory_tombstones
    import pending_suggestions
    import turn_forget

    async def flag(_u, _t):
        return 1

    class _NoOffers:
        async def fetch(self, *_a):
            return []

        async def execute(self, *_a):
            return None

    @contextlib.asynccontextmanager
    async def offers():
        yield _NoOffers()

    monkeypatch.setattr(pending_suggestions, "get_db_ctx", offers)
    monkeypatch.setattr(exact_words, "forget_transcript", flag)
    turn_forget.reset()
    memory_tombstones.clear_all()
    exact_words.set_backend(exact_words.MemoryBackend())
    yield
    exact_words.set_backend(None)
    turn_forget.reset()
    memory_tombstones.clear_all()


def test_topic_forget_through_the_real_path_removes_the_tied_and_the_named(db, forget_env):
    import forget_topic

    _seed(db)
    svc = _registry_svc((R(A, "r1n"), "User's daughter Juno is 8 years old and started school"))
    res = run(forget_topic.forget(svc, A, "juno's school"))
    assert not res.store_down and res.forgotten == 1
    assert _subjects(db, A) == [WREN] and _subjects(db, B) == [OTHER_JUNO]


SISTER = "Juno is 8 not 7, she is my sister"


def test_topic_forget_reaches_an_entry_recorded_before_any_row(db, forget_env):
    """Thread PRRT_kwDOPZpkks63cEWh: the entry has empty row_ids and the topic ("my sister") matches neither a row id nor the
    person's name; the forget retracts the turn it was said in, and that turn's id reaches it."""
    import forget_topic
    import turn_forget
    import value_correction

    _rec(A, JUNO, _age(7), _age(8), T0, turn=value_correction.turn_id_of(A, SISTER), rows=[])
    _rec(A, WREN, _age(3), _age(4), T0, turn=value_correction.turn_id_of(A, "wren is 4 not 3"), rows=[])
    _rec(B, OTHER_JUNO, _age(5), _age(6), T0, turn=value_correction.turn_id_of(B, SISTER), rows=[])
    turn_forget.note_user_turn(A, SISTER)
    res = run(forget_topic.forget(_registry_svc(), A, "my sister"))
    assert not res.store_down and res.turns_retracted == 1
    assert _subjects(db, A) == [WREN] and _subjects(db, B) == [OTHER_JUNO]


def test_without_the_turn_ids_the_entry_survives(db, forget_env, monkeypatch):
    """Control: the leak is real - with the turn ids dropped from the call, the entry outlives the forget."""
    import forget_topic
    import turn_forget
    import value_correction

    real = reg.erase_topic

    async def no_turns(uid, **kw):
        kw.pop("turn_ids", None)
        return await real(uid, **kw)
    monkeypatch.setattr(reg, "erase_topic", no_turns)
    _rec(A, JUNO, _age(7), _age(8), T0, turn=value_correction.turn_id_of(A, SISTER), rows=[])
    turn_forget.note_user_turn(A, SISTER)
    run(forget_topic.forget(_registry_svc(), A, "my sister"))
    assert _subjects(db, A) == [JUNO]


def test_erase_topic_by_turn_ids_over_forgets_what_it_cannot_rule_out(db):
    import value_correction

    _rec(A, JUNO, _age(7), _age(8), T0, turn=value_correction.turn_id_of(A, SISTER), rows=[])
    _rec(A, WREN, _age(3), _age(4), T0 + 1, rows=[])                                  # no source turn
    db.conn.execute("INSERT INTO people (id, user_id, name, deleted) VALUES (?, ?, 'Rook', 0)", (U("rook"), A))
    db.conn.commit()
    _rec(A, U("rook"), _age(9), _age(10), T0 + 2, turn=value_correction.turn_id_of(A, "rook is 10 not 9"), rows=[])
    assert run(reg.erase_topic(A, turn_ids=[value_correction.turn_id_of(A, "juno is 8 NOT 7,  she is my sister")])) == 4
    assert _subjects(db, A) == [U("rook")]
    assert run(reg.erase_topic(A, turn_ids=[None])) == 2 and _subjects(db, A) == []


def test_topic_forget_by_a_tied_row_alone(db):
    _seed(db)
    assert run(reg.erase_topic(A, row_ids=[R(A, "r2")], name_matches=lambda _n: False)) == 2
    assert _subjects(db, A) == [JUNO]


def test_topic_forget_fails_closed_when_the_registry_cannot_be_reached(db, forget_env, monkeypatch):
    import forget_topic

    async def down(*_a, **_k):
        raise ConnectionError("pool lost")
    monkeypatch.setattr(reg, "erase_topic", down)
    res = run(forget_topic.forget(_registry_svc((R(A, "r1n"), "User's daughter Juno is 8 years old and started school")), A, "juno's school"))
    assert res.store_down and res.forgotten == 0                         # nothing archived behind a registry that kept its rows


def _forget_last(monkeypatch, text, rows=()):
    import exact_words
    import intent_router as ir
    import memory_service
    import turn_forget

    class Row:
        def __init__(self, i):
            self.id, self.text = i, "a fact"

    class Svc:
        async def review(self, *a, **k):
            return object()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: Svc())

    async def from_turn(svc, uid, t, ts):
        return [Row(i) for i in rows]

    async def noop(*a, **k):
        return 0
    monkeypatch.setattr(turn_forget, "rows_from_turn", from_turn)
    monkeypatch.setattr(turn_forget, "previous_turn", lambda uid, **k: turn_forget.Turn(text, T0, "h", 1))
    monkeypatch.setattr(turn_forget, "retract", lambda *a, **k: None)
    monkeypatch.setattr(turn_forget, "mark_forgotten", lambda *a, **k: None)
    monkeypatch.setattr(exact_words, "erase_text", noop)
    monkeypatch.setattr(exact_words, "forget_transcript", noop)
    monkeypatch.setattr(memory_service, "physical_erase_enabled", lambda: False)
    return run(ir._execute_forget_last(A))


def test_forget_that_through_the_real_path_removes_what_that_turn_recorded(db, monkeypatch):
    from people_graph import content_turn_id

    said = "juno is 8 not 7"
    turn = content_turn_id(A, said)
    _rec(A, JUNO, _age(7), _age(8), T0, turn=turn, rows=[R(A, "r5")])
    _rec(A, WREN, _age(3), _age(4), T0, turn=T("other"), rows=[R(A, "r6")])
    assert _forget_last(monkeypatch, said).startswith("Done")
    assert _subjects(db, A) == [WREN]


def test_forget_that_also_reaches_entries_tied_to_the_turns_rows(db, monkeypatch):
    _rec(A, JUNO, _age(7), _age(8), T0, turn=T("not-this-text"), rows=[R(A, "r5")])
    _forget_last(monkeypatch, "something else entirely", rows=[R(A, "r5")])
    assert _subjects(db, A) == []


def test_forget_that_is_not_done_when_the_registry_cannot_be_reached(db, monkeypatch):
    import intent_router as ir

    async def down(*_a, **_k):
        raise ConnectionError("pool lost")
    monkeypatch.setattr(reg, "erase_for_turn", down)
    assert _forget_last(monkeypatch, "juno is 8 not 7") == ir.FORGET_STORE_DOWN_REPLY


def test_the_physical_row_erase_clears_tied_entries_before_the_rows_go(db, tmp_path, monkeypatch):
    import memory_service

    svc = memory_service.MemoryService(data_dir=str(tmp_path / "palace"))
    monkeypatch.setattr(svc, "_owned_rows_sync", lambda uid, ids: ([], []))
    _seed(db)
    assert run(svc.erase_rows(A, [R(A, "r1")], actor=A)) == {"rows_removed": 0}
    assert _subjects(db, A) == [WREN]

    async def down(*_a, **_k):
        raise ConnectionError("pool lost")
    monkeypatch.setattr(reg, "erase_for_rows", down)
    with pytest.raises(memory_service.MemoryServiceError):              # fail closed: no row is erased behind a kept entry
        run(svc.erase_rows(A, [R(A, "r2")], actor=A))


def test_every_eraser_is_a_no_op_on_a_store_that_was_never_migrated(tmp_path, monkeypatch):
    bare = _Sqlite(str(tmp_path / "bare.db"))
    bare.conn.executescript(PEOPLE + ";")

    @contextlib.asynccontextmanager
    async def fake():
        yield bare
    monkeypatch.setattr(db_compat, "get_compat_db", fake)
    assert run(reg.forget_user(A)) == 0
    assert run(reg.erase_topic(A, row_ids=["r"])) == 0
    assert run(reg.erase_for_turn(A, "x")) == 0
    assert run(reg.erase_for_rows(A, ["r"])) == 0


# -- the call sites, by AST, each with its break-the-fix control ------------------------------------------------------------------

#: (file, function that must call it, eraser): one per forget path. The member and entity paths are ALSO pinned by the forget-reach
#: inventory (forget_reach.TABLES); topic, turn and rows have no inventory column, so this list is their pin.
CALL_SITES = (
    ("memory_service.py", "delete_user", "forget_user"),
    ("memory_forget_cascade.py", "_run", "erase_entity"),
    ("forget_topic.py", "forget", "erase_topic"),
    ("intent_router.py", "_execute_forget_last", "erase_for_turn"),
    ("memory_service.py", "erase_rows", "erase_for_rows"),
)


def _calls(source: str, within: str, eraser: str) -> bool:
    tree = ast.parse(source)
    scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == within]
    assert scopes, f"no function {within}"
    for scope in scopes:
        for n in ast.walk(scope):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == eraser
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "memory_value_registry"):
                return True
    return False


@pytest.mark.parametrize("fname, within, eraser", CALL_SITES)
def test_every_forget_path_calls_its_registry_eraser(fname, within, eraser):
    src = (SVC / fname).read_text(encoding="utf-8")
    assert _calls(src, within, eraser), f"{fname}:{within} no longer calls memory_value_registry.{eraser}"
    # break-the-fix control: the same source with that one call renamed must go red (and a comment naming it is not a call)
    broken = src.replace(f"memory_value_registry.{eraser}(", f"memory_value_registry.NOT_{eraser}(")
    assert broken != src and not _calls(broken, within, eraser)
    assert not _calls(broken + f"\n# memory_value_registry.{eraser}(x)\n", within, eraser)


def test_the_forget_reach_inventory_lists_the_table_with_both_erasers():
    import forget_reach as fr

    (s,) = [s for s in fr.STORES if s.key == "memory_value_registry"]
    assert s.status == fr.REACHED and s.holds == fr.WORDS
    assert s.member == "memory_value_registry.forget_user" and s.entity == "memory_value_registry.erase_entity"
    assert "memory_value_registry" not in fr.GAPS


def test_the_inventory_would_catch_the_table_without_its_entry(monkeypatch):
    """Break-the-fix control: with the entry removed, the coverage check names the table."""
    import forget_reach as fr

    found = fr.discover_user_tables([MIG])
    assert found.get("memory_value_registry") == "user_id"
    monkeypatch.setattr(fr, "STORES", tuple(s for s in fr.STORES if s.key != "memory_value_registry"))
    monkeypatch.setattr(fr, "TABLES", tuple(s for s in fr.TABLES if s.key != "memory_value_registry"))
    assert "memory_value_registry" in fr.uncovered_tables(found)


# ── 5. the shadow wiring: no behaviour change with the flag off ─────────────────────────────

class _VcRow:
    def __init__(self, i, text):
        self.id, self.text, self.metadata = i, text, {"user_id": A}


class _VcSvc:
    def __init__(self, *texts):
        self.rows = [_VcRow(R(A, f"r{i}"), t) for i, t in enumerate(texts)]
        self.edits = []

    async def list_by_status(self, **_k):
        return list(self.rows)

    async def review(self, rid, decision=None, edits=None, **_k):
        self.edits.append((rid, edits))
        return _VcRow(R(A, "new-" + rid), edits)


def _vc_apply(monkeypatch, text, *texts):
    import correction_apply
    import user_prefs
    import value_correction as vc

    async def opted_in(_u):
        return False
    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    vc.reset_state()
    svc = _VcSvc(*texts)
    return vc.apply, svc, run(vc.apply(text, A, None, svc=svc))


def test_flag_off_records_nothing_reads_nothing_and_decides_the_same(db, monkeypatch):
    import people_graph

    async def never(*_a, **_k):
        raise AssertionError("no registry read or write with the flag off")
    monkeypatch.setattr(reg, "record", never)
    monkeypatch.setattr(people_graph, "resolve_person", never)
    _f, svc, n = _vc_apply(monkeypatch, "juno is 8 not 7", "User's daughter Juno is 7 years old")
    assert n == 1 and svc.edits == [(R(A, "r0"), "User's daughter Juno is 8 years old")]
    assert db.q("SELECT COUNT(*) FROM memory_value_registry") == [(0,)]


def test_shadow_records_an_age_by_person_id_and_the_old_path_still_decides(db, monkeypatch):
    from people_graph import content_turn_id

    monkeypatch.setenv(reg.FLAG, "shadow")
    _f, svc, n = _vc_apply(monkeypatch, "juno is 8 not 7", "User's daughter Juno is 7 years old")
    assert n == 1 and svc.edits == [(R(A, "r0"), "User's daughter Juno is 8 years old")]          # unchanged decision
    rows = db.q("SELECT subject_ref, attribute, role, value_norm, source_turn_id, row_ids FROM memory_value_registry ORDER BY role")
    tied = json.dumps([R(A, "r0"), R(A, "new-" + R(A, "r0"))])
    assert rows == [(JUNO, "age", "asserted", '{"v":8.0}', content_turn_id(A, "juno is 8 not 7"), tied),
                    (JUNO, "age", "superseded", '{"v":7.0}', content_turn_id(A, "juno is 8 not 7"), tied)]


@pytest.mark.parametrize("text", [
    "my dad is 76 not 78",                            # a relation: v1 does not resolve it
    "the dentist is on the 27th not the 20th",        # a label and a date
    "ju is 8 not 7",                                  # a prefix of Juno: never a prefix match
    "zara is 8 not 7",                                # nobody of that name
])
def test_shadow_records_nothing_it_cannot_tie_to_one_person_without_words(db, monkeypatch, text):
    monkeypatch.setenv(reg.FLAG, "shadow")
    _vc_apply(monkeypatch, text, "User's daughter Juno is 7 years old")
    assert db.q("SELECT COUNT(*) FROM memory_value_registry") == [(0,)]


def test_the_idle_shadow_check_agrees_and_disagrees_counts_only(db, monkeypatch, caplog):
    monkeypatch.setenv(reg.FLAG, "shadow")
    _rec(A, JUNO, _age(7), _age(8), T0)
    caplog.set_level(logging.INFO, logger=reg.__name__)
    fact = "User's daughter Juno is 7 years old"
    assert run(reg.shadow_check(A, fact, T0 - 10, True, now=T0)) == "agree"
    assert run(reg.shadow_check(A, fact, T0 + 10, False, now=T0)) == "agree"          # said after the correction
    assert run(reg.shadow_check(A, fact, T0 - 10, False, now=T0)) == "registry_only"  # e.g. the in-process registry lost it in a restart
    assert run(reg.shadow_check(A, "User's son Wren is 3", T0 - 10, True, now=T0)) == "old_only"
    lines = [r.getMessage() for r in caplog.records if "MEMORY_SUPERSEDED_SHADOW" in r.getMessage()]
    assert len(lines) == 4
    monkeypatch.setenv(reg.FLAG, "off")
    assert run(reg.shadow_check(A, fact, T0 - 10, True, now=T0)) == ""


# ── 6. privacy: no row and no log line carries free text, a name or a member id ────────────

def test_no_row_or_log_line_carries_words_names_or_ids_of_the_member(db, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv(reg.FLAG, "shadow")
    said = "Juno is 8 not 7"
    _vc_apply(monkeypatch, said, "User's daughter Juno is 7 years old")
    _rec(A, WREN, _age(3), _age(4), T0)
    run(reg.is_superseded(A, JUNO, "age", _age(7), T0 - 10))
    run(reg.shadow_check(A, "User's daughter Juno is 7 years old", T0 - 10, True))
    run(reg.erase_topic(A, row_ids=[R(A, "r0")]))
    run(reg.forget_user(A))
    # every logged line of the module: fixed codes and counts only
    mine = [r.getMessage() for r in caplog.records if r.name == reg.__name__]
    assert mine
    for line in mine:
        for needle in ("Juno", "juno", "Wren", "daughter", A, said):
            assert needle not in line, line
        assert re.fullmatch(r"[A-Z_]+(?: [a-z_]+=[a-z_0-9,-]+)*|memory_value_registry: [a-z ]+\([A-Za-z]+\)|[A-Z_]+ \([A-Za-z]+\)", line), line
    # every stored cell: ids, closed labels, numbers - never a name or a word of the turn. Name-shaped SINGLE tokens are offered in
    # every id column (a token is a valid "id-looking" string, which is exactly what the shape checks exist to refuse)
    for kw in ({"turn": "Juno"}, {"claim": "juno"}, {"rows": ["Wren"]}, {"rows": ["daughter"]}, {"subject": "Juno"}):
        assert run(reg.record(A, kw.get("subject", JUNO), "age", superseded=[_age(7)], asserted=_age(8), said_at=T0,
                              by_claim=kw.get("claim", C(9)), source_turn_id=kw.get("turn", T("9")),
                              row_ids=kw.get("rows", [R(A, "r9")]), now=T0)) == 0
    _rec(A, JUNO, _age(7), _age(8), T0, turn=T("1234"), rows=[R(A, "r1")])
    cells = [c for row in db.q("SELECT * FROM memory_value_registry") for c in row]
    assert cells
    for c in cells:
        s = str(c)
        assert not re.search(r"(?i)juno|wren|daughter|years old", s), s
    for (v,) in db.q("SELECT value_norm FROM memory_value_registry"):
        parsed = json.loads(v)
        assert all(isinstance(x, (int, float)) for x in parsed.values())


def test_the_module_logs_only_through_fixed_format_strings():
    """No f-string or %-format in a log call can put a subject or a value in a line: every logger call's first argument is a plain
    string constant whose %s placeholders are fed only counts, attribute codes, verdict codes or exception class names."""
    tree = ast.parse((SVC / "memory_value_registry.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "logger"]
    assert calls
    for c in calls:
        assert isinstance(c.args[0], ast.Constant) and isinstance(c.args[0].value, str)
        for a in c.args[1:]:
            text = ast.unparse(a)
            assert not re.search(r"\b(uid|user_id|subject_ref|value|value_norm|fact|name|names|turn_text|by_claim)\b", text), text
