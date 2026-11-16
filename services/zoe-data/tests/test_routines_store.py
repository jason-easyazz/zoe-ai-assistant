"""Routines stage 1, the store (``routines_store.py``, alembic 0081). Synthetic members only.

Every rule has a negative control beside it: the arm that proves the check can fail (another person, a guest, a family routine
asked to hold a medicine line, an inventory with the entries removed).
"""
from __future__ import annotations

import ast
import asyncio
import re
import sqlite3
from pathlib import Path

import pytest

import forget_reach as fr
import memory_forgotten
import routines_store as rs
from routines_support import ALEX, RILEY, SAM, migrate, run, seed_day, store  # noqa: F401 - store is a fixture

pytestmark = pytest.mark.ci_safe

#: odd text: dashes, a fraction, comparison signs, tilde, degree, pipes, markdown marks, an ISO date, edge spaces, a newline
ODD = "  Tablets – 1–2 (½) > 10 mg; ~5°C | *not* _this_ — 2026-11-14 \n then water  "
TABLES = {"routines", "routine_steps", "routine_days", "routine_day_steps"}


def _mk(owner=ALEX, steps=("feed the cat", "bins"), **kw):
    return run(rs.create_routine(owner, "Morning", "waking", steps=steps, **kw))


# -- the migration -----------------------------------------------------------------------------------------------------------------
def test_migration_up_is_idempotent_and_down_drops_everything(tmp_path):
    path = str(tmp_path / "m.db")
    migrate(path)
    migrate(path)                                  # a rerun is a no-op (IF NOT EXISTS)
    names = {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert TABLES <= names
    migrate(path, "downgrade")
    names = {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not names & TABLES


def test_the_flag_is_registered_and_off_by_default(monkeypatch):
    monkeypatch.delenv("ZOE_ROUTINES", raising=False)
    assert rs.enabled() is False
    monkeypatch.setenv("ZOE_ROUTINES", "on")
    assert rs.enabled() is True
    monkeypatch.setenv("ZOE_ROUTINES", "maybe")    # an unlisted value falls back to the default: never a silent flip on
    assert rs.enabled() is False


# -- verbatim ----------------------------------------------------------------------------------------------------------------------
def test_odd_text_round_trips_exactly(store):
    r = _mk(steps=(ODD,))
    assert r["steps"][0]["text"] == ODD
    added = run(rs.add_step(ALEX, r["id"], ODD))
    assert added["text"] == ODD
    assert store.q("SELECT text FROM routine_steps") == [(ODD,), (ODD,)]
    assert [s["text"] for s in run(rs.get_routine(ALEX, r["id"]))["steps"]] == [ODD, ODD]


def test_only_empty_or_over_long_text_is_refused(store):
    for bad in ("", "   ", None, "x" * (rs.MAX_TEXT + 1)):
        with pytest.raises(rs.StoreError):
            _mk(steps=(bad,))
    assert store.q("SELECT COUNT(*) FROM routines") == [(0,)]


# -- visibility --------------------------------------------------------------------------------------------------------------------
def test_personal_is_the_subjects_and_family_is_everyones(store):
    mine = _mk()
    fam = run(rs.create_routine(SAM, "Feed the dog", "dinner", visibility="family", steps=("feed the dog",)))
    assert {r["id"] for r in run(rs.list_routines(ALEX))} == {mine["id"], fam["id"]}
    assert {r["id"] for r in run(rs.list_routines(SAM))} == {fam["id"]}           # control: Sam never sees Alex's own
    assert {r["id"] for r in run(rs.list_routines(None))} == {fam["id"]}          # the unidentified panel: family only
    assert {r["id"] for r in run(rs.list_routines("guest"))} == {fam["id"]}
    assert run(rs.get_routine(SAM, mine["id"])) is None


def test_a_guest_owns_nothing_and_medicine_never_goes_on_a_family_routine(store):
    with pytest.raises(rs.StoreError) as e:
        run(rs.create_routine("guest", "Morning", "waking"))
    assert e.value.code == "guest_owns_nothing"
    with pytest.raises(rs.StoreError) as e:
        run(rs.create_routine(ALEX, "House", "dinner", visibility="family", steps=(("tablets", "medicine"),)))
    assert e.value.code == "medicine_not_on_family"
    fam = run(rs.create_routine(ALEX, "House", "dinner", visibility="family"))
    with pytest.raises(rs.StoreError):
        run(rs.add_step(SAM, fam["id"], "tablets", "medicine"))
    run(rs.add_step(SAM, fam["id"], "wipe the bench"))                            # any member may add an ordinary line
    with pytest.raises(rs.StoreError):
        run(rs.add_step(SAM, _mk()["id"], "x"))                                   # not on someone else's personal routine
    for bad in ({"anchor": "noon"}, {"window_start": "25:00"}, {"days_rule": "FREQ=SOMETIMES"}):
        with pytest.raises(rs.StoreError):
            run(rs.create_routine(ALEX, "Bad", **{"anchor": "window", **bad}))


# -- forget ------------------------------------------------------------------------------------------------------------------------
def _count(store, table, where="1 = 1", params=()):
    return store.q(f"SELECT COUNT(*) FROM {table} WHERE {where}", params)[0][0]


def test_forget_user_erases_every_routine_of_the_subject_and_blanks_them_elsewhere(store):
    r = run(rs.create_routine(SAM, "Morning", "waking", steps=(("tablets", "medicine"), "stretch")))
    kid = run(rs.create_routine(RILEY, "School", "leaving", created_by=SAM, steps=("bag",)))   # Sam set it up for Riley
    keep = _mk()
    sam_day = seed_day(store, r["id"], SAM, ("tablets", "stretch"), actor=SAM)
    kid_day = seed_day(store, kid["id"], RILEY, ("bag",), actor=SAM)                            # Sam ticked Riley's line
    keep_day = seed_day(store, keep["id"], ALEX, ("feed the cat",), actor=ALEX)
    assert run(rs.forget_user(SAM)) == 1
    for table in ("routines",):
        assert _count(store, table, "user_id = ?", (SAM,)) == 0
    assert _count(store, "routine_steps", "routine_id = ?", (r["id"],)) == 0
    assert _count(store, "routine_days", "user_id = ? OR id = ?", (SAM, sam_day)) == 0                    # both day tables
    assert _count(store, "routine_day_steps", "day_id = ? OR actor_user_id = ?", (sam_day, SAM)) == 0
    assert store.q("SELECT created_by FROM routines WHERE id = ?", (kid["id"],)) == [(RILEY,)]
    assert store.q("SELECT text, actor_user_id FROM routine_day_steps WHERE day_id = ?", (kid_day,)) == [("bag", None)]
    assert run(rs.get_routine(ALEX, keep["id"])) is not None                       # control: nobody else's routine went
    assert _count(store, "routine_day_steps", "day_id = ? AND actor_user_id = ?", (keep_day, ALEX)) == 1
    assert run(rs.get_routine(RILEY, kid["id"]))["steps"][0]["text"] == "bag"


def test_forget_user_without_tables_is_nothing_and_a_db_error_raises(monkeypatch, tmp_path):
    import contextlib

    import db_compat
    from routines_support import Sqlite

    empty = Sqlite(str(tmp_path / "empty.db"))

    @contextlib.asynccontextmanager
    async def fake():
        yield empty

    monkeypatch.setattr(db_compat, "get_compat_db", fake)
    assert run(rs.forget_user(SAM)) == 0

    @contextlib.asynccontextmanager
    async def broken():
        raise RuntimeError("connection reset")
        yield

    monkeypatch.setattr(db_compat, "get_compat_db", broken)
    with pytest.raises(RuntimeError):
        run(rs.forget_user(SAM))                                                   # fail closed: delete_user is retried


def test_erase_entity_deletes_routines_steps_days_and_day_lines_that_name_the_entity(store):
    named = run(rs.create_routine(ALEX, "Quinn visit", "window"))
    r = _mk(steps=("call Quinn", "bins"))
    named_day = seed_day(store, named["id"], ALEX, ("tidy up",))
    day = seed_day(store, r["id"], ALEX, ("text quinn back", "bins"))
    rx = re.compile(r"\bquinn\b", re.I)
    assert run(rs.erase_entity(ALEX, rx, strict=True)) == 3
    assert run(rs.get_routine(ALEX, named["id"])) is None
    assert _count(store, "routine_days", "id = ?", (named_day,)) == 0                  # the named routine's day copy went with it
    assert _count(store, "routine_day_steps", "day_id = ?", (named_day,)) == 0
    assert [s["text"] for s in run(rs.get_routine(ALEX, r["id"]))["steps"]] == ["bins"]     # control: the other line stays
    assert store.q("SELECT text FROM routine_day_steps WHERE day_id = ?", (day,)) == [("bins",)]
    assert _count(store, "routine_days", "id = ?", (day,)) == 1                         # a date and times only: kept
    for table, col in (("routines", "name"), ("routine_steps", "text"), ("routine_day_steps", "text")):
        assert not [t for (t,) in store.q(f"SELECT {col} FROM {table}") if rx.search(t)]
    assert run(rs.erase_entity(SAM, rx, strict=True)) == 0                                  # only the member's own


# -- forget wins every race (the one write gate) -----------------------------------------------------------------------------------
def _forget(member, name):
    """What the forget intent does BEFORE the cascade runs: the durable ledger entry (salted hash only)."""
    assert run(memory_forgotten.add(member, name))


def _hook_rows(monkeypatch, marker, action):
    """Run ``action`` (async) ONCE, right after the first ``_rows`` query whose SQL starts with ``marker`` returned."""
    real, fired = rs._rows, []

    async def rows(db, sql, params=()):
        out = await real(db, sql, params)
        if not fired and sql.startswith(marker):
            fired.append(1)
            await action()
        return out
    monkeypatch.setattr(rs, "_rows", rows)
    return fired


def test_a_step_added_while_the_entity_erase_runs_never_survives_it(store, monkeypatch):
    """The race: the erase has scanned the member's routines; a writer adds "call Quinn" before the erase finishes. Old code: the
    erase held no gate, the insert landed after the scan and the line survived a forget that reported success."""
    r = _mk(steps=("bins",))
    _forget(ALEX, "Quinn")
    rx = re.compile(r"\bquinn\b", re.I)
    task = []

    async def writer_starts():
        task.append(asyncio.ensure_future(rs.add_step(ALEX, r["id"], "call Quinn")))
        for _ in range(60):
            await asyncio.sleep(0)                                   # let it get as far as it can

    async def scenario():
        fired = _hook_rows(monkeypatch, "SELECT id, name FROM routines WHERE user_id", writer_starts)
        await rs.erase_entity(ALEX, rx, strict=True)
        assert fired
        with pytest.raises(rs.StoreError) as e:
            await task[0]
        assert e.value.code == "names_forgotten"
    run(scenario())
    assert not [t for (t,) in store.q("SELECT text FROM routine_steps") if rx.search(t)]
    assert [s["text"] for s in run(rs.get_routine(ALEX, r["id"]))["steps"]] == ["bins"]


def test_a_write_that_began_before_a_member_erase_is_refused_after_it(store, monkeypatch):
    """add_step has read the routine; the member's delete runs to completion; the write then lands. Old code snapshotted the erase
    generation only when it reached the gate (after the erase), so the stale insert landed for a deleted routine."""
    r = run(rs.create_routine(SAM, "Morning", "waking", steps=("stretch",)))

    async def scenario():
        _hook_rows(monkeypatch, "SELECT id, user_id, created_by", lambda: rs.forget_user(SAM))
        with pytest.raises(rs.StoreError) as e:
            await rs.add_step(SAM, r["id"], "water the plants")
        assert e.value.code in ("erased", "not_found")
    run(scenario())
    for table in ("routines", "routine_steps", "routine_days", "routine_day_steps"):
        assert _count(store, table) == 0


def test_a_write_that_began_before_an_entity_forget_is_refused_after_it(store, monkeypatch):
    r = _mk(steps=("bins",))

    async def forget_now():
        await memory_forgotten.add(ALEX, "Quinn")
        await rs.erase_entity(ALEX, re.compile(r"\bquinn\b", re.I), strict=True)

    async def scenario():
        _hook_rows(monkeypatch, "SELECT id, user_id, created_by", forget_now)
        with pytest.raises(rs.StoreError) as e:
            await rs.add_step(ALEX, r["id"], "ring Quinn about Sunday")
        assert e.value.code == "names_forgotten"
    run(scenario())
    assert [s["text"] for s in run(rs.get_routine(ALEX, r["id"]))["steps"]] == ["bins"]


def test_a_later_fresh_write_naming_a_forgotten_entity_is_refused(store):
    r = _mk(steps=("bins",))
    _forget(ALEX, "Quinn")
    for call in (lambda: rs.add_step(ALEX, r["id"], "call QUINN"),
                 lambda: rs.create_routine(ALEX, "Quinn visit", "window"),
                 lambda: rs.create_routine(ALEX, "Evening", "bed", steps=("text quinn",))):
        with pytest.raises(rs.StoreError) as e:
            run(call())
        assert e.value.code == "names_forgotten"
    assert run(rs.add_step(ALEX, r["id"], "call Quincy"))["text"] == "call Quincy"   # control: whole words only
    assert run(rs.add_step(SAM, _mk(owner=SAM)["id"], "call Quinn"))                 # control: Sam forgot nobody
    assert _count(store, "routines") == 2


def test_the_ledger_being_down_refuses_the_write(store, monkeypatch):
    r = _mk()

    async def down(*_a, **_k):
        raise memory_forgotten.LedgerUnavailable("down")
    monkeypatch.setattr(memory_forgotten, "active_hashes", down)
    with pytest.raises(rs.StoreError) as e:
        run(rs.add_step(ALEX, r["id"], "bins again"))
    assert e.value.code == "ledger_unavailable"


_WRITE_SQL = re.compile(r"^\s*(?:INSERT|UPDATE|DELETE)\b", re.I)
_GUARDS = {"_guarded_write", "_guarded_erase"}


def _ungated_writers(source: str) -> list[str]:
    """Every module-level function that issues write SQL (itself or through a ``db`` / ``w``-taking helper) but does not open one of
    the two guards. A helper whose first parameter is ``db`` / ``w`` runs inside its caller's guard and is checked through its callers."""
    tree = ast.parse(source)
    fns = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def writes(fn) -> bool:
        return any(isinstance(c, ast.Constant) and isinstance(c.value, str) and _WRITE_SQL.match(c.value) for c in ast.walk(fn))

    def calls(fn) -> set:
        return {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}

    def inner(fn) -> bool:
        return bool(fn.args.args) and fn.args.args[0].arg in ("db", "w")

    carrying = {n for n, f in fns.items() if writes(f)}
    while True:       # a write travels up through the db-taking helpers only (a caller of a GUARDED function is not a writer)
        more = {n for n, f in fns.items() if n not in carrying and any(c in carrying and inner(fns[c]) for c in calls(f))}
        if not more:
            break
        carrying |= more
    return sorted(n for n in carrying if n not in _GUARDS and not inner(fns[n]) and not calls(fns[n]) & _GUARDS)


def test_every_write_path_goes_through_the_one_gate():
    src = Path(rs.__file__).read_text()
    assert _ungated_writers(src) == []
    tree = ast.parse(src)
    fns = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {"create_routine", "add_step", "forget_user", "erase_entity"} <= fns          # the enumeration is not vacuous
    # negative control: a writer that skips the gate is caught, directly or through a db helper
    rogue = src + (
        "\n\nasync def rename_routine(actor, rid, name):\n    async with _db() as db:\n"
        "        await db.execute(\"UPDATE routines SET name = ? WHERE id = ?\", (name, rid))\n"
        "\n\nasync def purge(rid):\n    async with _db() as db:\n        await _delete_routines(db, [rid])\n")
    assert _ungated_writers(rogue) == ["purge", "rename_routine"]


# -- the migration chain -----------------------------------------------------------------------------------------------------------
def test_the_migration_chain_has_one_head_and_this_revision_hangs_off_a_real_parent():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(rs.__file__).parent / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, heads
    me = script.get_revision("0081")
    assert me.down_revision == "0080" and script.get_revision(me.down_revision) is not None
    assert "on 0080" in Path(me.path).read_text()                                    # the docstring tells the same chain


def test_the_forget_inventory_reaches_every_routine_table_and_would_catch_a_missing_entry():
    found = fr.discover_user_tables([rs.__file__.replace("routines_store.py", "alembic/versions/0081_routines.py")])
    assert TABLES == set(found)                                                     # the discovery sees all four (children too)
    assert not set(fr.uncovered_tables(found)) & TABLES
    assert all(fr.BY_KEY[k].status == fr.REACHED and fr.BY_KEY[k].member == "routines_store.forget_user" for k in TABLES)
    stripped = {s.key for s in fr.TABLES if s.key not in TABLES}                   # negative control: drop the entries
    assert set(found) - stripped == TABLES
