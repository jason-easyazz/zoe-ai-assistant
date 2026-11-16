"""Routines stage 1, the store (``routines_store.py``, alembic 0081). Synthetic members only.

Every rule has a negative control beside it: the arm that proves the check can fail (another person, a guest, a family routine
asked to hold a medicine line, an inventory with the entries removed).
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
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


def _as_another_process(monkeypatch):
    """This process's erase gate sees nothing (no lock, no generation): what a SECOND process looks like to the gate. Only the
    durable marks re-read inside the write's transaction can then stop a stale write."""
    import user_erase_gate as eg

    @contextlib.asynccontextmanager
    async def writing(_m, _g):
        yield True

    @contextlib.asynccontextmanager
    async def erasing(_m, *, bump=True):
        yield

    monkeypatch.setattr(eg, "writing", writing)
    monkeypatch.setattr(eg, "erasing", erasing)
    monkeypatch.setattr(eg, "generation", lambda _m: 0)
    monkeypatch.setattr(eg, "in_progress", lambda _m: False)
    monkeypatch.setattr(eg, "snapshot", dict)


async def _ledger_row_from_another_process(member, name):
    """The forget recorded by ANOTHER process: straight into the durable store, so this process's 30 s cache is NOT invalidated."""
    await memory_forgotten.get_backend().upsert(member, memory_forgotten.key_hash(member, name), scope="entity", actor=member,
                                                forgotten_at="2026-11-12T07:00:00Z", shield_until=memory_forgotten.PERMANENT_UNTIL)


def test_a_forget_recorded_by_another_process_is_seen_by_the_re_read_under_the_lock(store, monkeypatch):
    """The reviewer's case: this process's ledger view is cached (empty); another process records "forget Quinn" while the write
    waits; the write then takes the lock. Old code decided from the stale cached view and inserted the line."""
    r = _mk(steps=("bins",))
    _as_another_process(monkeypatch)
    assert run(memory_forgotten.active_hashes(ALEX)) == frozenset()            # the cached view: nothing forgotten yet

    async def scenario():
        _hook_rows(monkeypatch, "SELECT id, user_id, created_by", lambda: _ledger_row_from_another_process(ALEX, "Quinn"))
        with pytest.raises(rs.StoreError) as e:
            await rs.add_step(ALEX, r["id"], "call Quinn")
        assert e.value.code == "names_forgotten"
    run(scenario())
    assert run(memory_forgotten.active_hashes(ALEX)) == frozenset()            # the cache really was stale throughout
    assert [s["text"] for s in run(rs.get_routine(ALEX, r["id"]))["steps"]] == ["bins"]


def test_a_member_erased_by_another_process_after_the_write_began_refuses_it(store, monkeypatch):
    """The durable member mark (``erase_watermark``, recorded by ``delete_user`` before it erases) is re-read under the lock: a write
    that STARTED before it is refused for every member involved; one that started after it is new activity and proceeds."""
    import erase_watermark

    fam = run(rs.create_routine(ALEX, "House", "dinner", visibility="family", steps=("feed the dog",)))
    _as_another_process(monkeypatch)
    seen = rs._start()
    run(erase_watermark.record(SAM))                                             # Sam (the ACTOR) erased elsewhere, meanwhile

    async def write(seen_):
        async with rs._guarded_write(seen_, subject=ALEX, actors=(SAM,)) as w:
            await w.db.execute("UPDATE routines SET updated_at = 'x' WHERE id = ?", (fam["id"],))
    with pytest.raises(rs.StoreError) as e:
        run(write(seen))
    assert e.value.code == "erased"
    run(write(rs._start()))                                                      # control: started after the mark


def test_an_unreadable_erase_watermark_refuses_the_write(store, monkeypatch):
    import erase_watermark

    r = _mk()

    class Down(erase_watermark.MemoryBackend):
        async def get(self, user_id):
            raise RuntimeError("down")
    erase_watermark.set_backend(Down())
    with pytest.raises(rs.StoreError) as e:
        run(rs.add_step(ALEX, r["id"], "bins again"))
    assert e.value.code == "ledger_unavailable"


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
    assert {"create_routine", "add_step", "forget_user", "erase_entity", "day_copy", "add_one_off", "note_named", "_apply"} <= fns
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


# == the day: copies, one-off lines, the walk and the ticks (1b) ===================================================================
import logging  # noqa: E402
from datetime import timedelta  # noqa: E402

from routines_support import THU  # noqa: E402

TODAY = THU.date()
TOMORROW = TODAY + timedelta(days=1)


def test_odd_text_round_trips_exactly_through_the_day_copy_and_a_one_off(store):
    r = _mk(steps=(ODD,))
    assert run(rs.day_copy(ALEX, r["id"]))["steps"][0]["text"] == ODD
    off = run(rs.add_one_off(ALEX, r["id"], TOMORROW, ODD))
    assert off["text"] == ODD
    assert store.q("SELECT text FROM routine_day_steps WHERE id = ?", (off["id"],)) == [(ODD,)]


# -- day copies --------------------------------------------------------------------------------------------------------------------
def test_the_day_copy_is_lazy_idempotent_and_frozen_once_made(store):
    r = _mk()
    assert store.q("SELECT COUNT(*) FROM routine_days") == [(0,)]                 # nothing until first need
    a = run(rs.day_copy(ALEX, r["id"]))
    b = run(rs.day_copy(ALEX, r["id"]))
    assert [s["id"] for s in a["steps"]] == [s["id"] for s in b["steps"]] and len(a["steps"]) == 2
    assert store.q("SELECT COUNT(*) FROM routine_days") == [(1,)]
    run(rs.add_step(ALEX, r["id"], "water the plants"))
    assert len(run(rs.day_copy(ALEX, r["id"]))["steps"]) == 2                     # today's copy is not rewritten
    assert len(run(rs.day_copy(ALEX, r["id"], TOMORROW))["steps"]) == 0           # a future day snapshots nothing yet


def test_a_one_off_joins_exactly_one_day(store, monkeypatch):
    r = _mk()
    off = run(rs.add_one_off(ALEX, r["id"], TOMORROW, "take the drill"))
    assert store.q("SELECT snapshot_at FROM routine_days WHERE id = ?", (off["day_id"],)) == [(None,)]
    monkeypatch.setattr(rs, "_now_utc", lambda: THU + timedelta(days=1))
    tom = run(rs.day_copy(ALEX, r["id"]))
    assert [(s["text"], s["today_only"]) for s in tom["steps"]] == [("feed the cat", False), ("bins", False), ("take the drill", True)]
    monkeypatch.setattr(rs, "_now_utc", lambda: THU + timedelta(days=2))
    assert "take the drill" not in [s["text"] for s in run(rs.day_copy(ALEX, r["id"]))["steps"]]
    with pytest.raises(rs.StoreError) as e:
        run(rs.add_one_off(ALEX, r["id"], TODAY, "too late"))                     # now in the past
    assert e.value.code == "past_day"
    with pytest.raises(rs.StoreError):
        run(rs.add_one_off(ALEX, r["id"], THU.date() + timedelta(days=3), "tablets", kind="medicine"))
    with pytest.raises(rs.StoreError):
        run(rs.add_one_off(SAM, r["id"], THU.date() + timedelta(days=3), "x"))    # not Sam's routine: not even visible


def test_a_day_the_routine_does_not_run_gets_no_copy_and_no_one_off(store):
    r = _mk(days_rule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR")
    sat = TODAY + timedelta(days=(5 - TODAY.weekday()) % 7)
    assert sat.weekday() == 5
    assert run(rs.day_copy(ALEX, r["id"], sat)) is None
    with pytest.raises(rs.StoreError) as e:
        run(rs.add_one_off(ALEX, r["id"], sat, "take the drill"))
    assert e.value.code == "not_that_day"
    assert run(rs.day_copy(ALEX, r["id"], TOMORROW)) is not None                  # control: a Friday runs


def test_a_missed_line_is_counted_when_the_next_day_is_opened(store, monkeypatch, caplog):
    r = _mk()
    run(rs.day_copy(ALEX, r["id"]))
    caplog.set_level(logging.INFO, logger="routines_store")
    monkeypatch.setattr(rs, "_now_utc", lambda: THU + timedelta(days=1))
    run(rs.day_copy(ALEX, r["id"]))
    missed = [m.getMessage() for m in caplog.records if "event=missed" in m.getMessage()]
    assert missed and "n=2" in missed[0]
    assert store.q("SELECT close_reason FROM routine_days WHERE day = ?", (TODAY.isoformat(),)) == [("window_end",)]


# -- ticks -------------------------------------------------------------------------------------------------------------------------
def _first(r, who=ALEX):
    return run(rs.day_copy(who, r["id"]))["steps"][0]["id"]


def test_a_said_tick_ticks_only_the_step_just_named_for_that_person_in_that_session(store, monkeypatch):
    r = _mk()
    ds = _first(r)
    with pytest.raises(rs.StoreError) as e:                                        # nothing named: "yep" ticks nothing
        run(rs.act_said("s1", ALEX, "done", verified=False))
    assert e.value.code == "nothing_named"
    run(rs.note_named("s1", ALEX, ds))
    for sess, who in (("s2", ALEX), ("s1", SAM)):                                  # another session, another person
        with pytest.raises(rs.StoreError):
            run(rs.act_said(sess, who, "done", verified=True))
    t0 = rs._mono()
    monkeypatch.setattr(rs, "_mono", lambda: t0 + rs.WALK_S + 1)                   # the walk went stale
    with pytest.raises(rs.StoreError):
        run(rs.act_said("s1", ALEX, "done", verified=True))
    assert not rs._NAMED                                                           # a stale walk is gone for good (aged on read)
    monkeypatch.setattr(rs, "_mono", lambda: t0 + 5)
    run(rs.note_named("s1", ALEX, ds))                                             # named again
    out = run(rs.act_said("s1", ALEX, "done", verified=False))
    assert out["state"] == "done"
    assert store.q("SELECT state, known_by, actor_user_id, verified, prompts FROM routine_day_steps WHERE id = ?", (ds,)) == [
        ("done", "said", ALEX, 0, 2)]
    assert store.q("SELECT COUNT(*) FROM routine_day_steps WHERE state = 'done'") == [(1,)]   # exactly one line changed
    with pytest.raises(rs.StoreError):                                             # the walk closed with the tick
        run(rs.act_said("s1", ALEX, "done", verified=False))


def test_skip_snooze_and_undo(store):
    r = _mk()
    ds = _first(r)
    run(rs.note_named("s1", ALEX, ds))
    assert run(rs.act_said("s1", ALEX, "snooze", verified=False))["state"] == "snoozed"
    run(rs.note_named("s1", ALEX, ds))
    assert run(rs.act_said("s1", ALEX, "skip", verified=False))["state"] == "skipped"
    assert run(rs.undo_said("s1", ALEX, verified=False))["state"] == "pending"
    with pytest.raises(rs.StoreError):
        run(rs.undo_said("s1", ALEX, verified=False))                              # one undo per change
    assert run(rs.act_said("s1", ALEX, "done", verified=False))["state"] == "done"  # undo re-names the line


def test_a_medicine_step_needs_its_subject_verified(store):
    r = run(rs.create_routine(SAM, "Morning", "waking", steps=(("tablets", "medicine"),)))
    ds = _first(r, SAM)
    run(rs.note_named("s1", SAM, ds))
    with pytest.raises(rs.StoreError) as e:
        run(rs.act_said("s1", SAM, "done", verified=False))
    assert e.value.code == "unverified"
    assert store.q("SELECT state FROM routine_day_steps WHERE id = ?", (ds,)) == [("pending",)]
    with pytest.raises(rs.StoreError):
        run(rs.act_tapped(None, ds, "done", verified=False))                       # a household tap: not even visible
    with pytest.raises(rs.StoreError):
        run(rs.act_tapped(ALEX, ds, "done", verified=True))                        # another member
    run(rs.note_named("s1", SAM, ds))
    assert run(rs.act_said("s1", SAM, "done", verified=True))["state"] == "done"
    # the wrong-tick invariant as a database check: no medicine line is ever done unverified by voice
    assert store.q("SELECT COUNT(*) FROM routine_day_steps WHERE kind = 'medicine' AND state = 'done' AND verified = 0") == [(0,)]


def test_the_fridge_rule_on_a_family_routine_and_never_on_a_personal_one(store):
    fam = run(rs.create_routine(SAM, "Feed the dog", "dinner", visibility="family", steps=("feed the dog",)))
    assert run(rs.act_tapped(None, _first(fam, None), "done", verified=False))["state"] == "done"
    mine = _mk()
    with pytest.raises(rs.StoreError):
        run(rs.act_tapped(None, _first(mine), "done", verified=False))
    with pytest.raises(rs.StoreError):
        run(rs.act_tapped(SAM, _first(mine), "done", verified=True))
    assert run(rs.act_tapped(ALEX, _first(mine), "done", verified=True))["state"] == "done"
    with pytest.raises(rs.StoreError) as e:
        run(rs.act_tapped(ALEX, _first(mine), "done", verified=True))               # a second tap never double-acts
    assert e.value.code == "already_done"


def test_logs_carry_codes_and_counts_never_the_words(store, caplog):
    caplog.set_level(logging.INFO, logger="routines_store")
    r = run(rs.create_routine(SAM, "Sunrise", "waking", steps=(("blue tablets", "medicine"),)))
    ds = _first(r, SAM)
    run(rs.add_one_off(SAM, r["id"], TOMORROW, "take the orange umbrella"))
    run(rs.note_named("s1", SAM, ds))
    with pytest.raises(rs.StoreError):
        run(rs.act_said("s1", SAM, "done", verified=False))
    text = " ".join(m.getMessage() for m in caplog.records)
    for event in ("created", "one_off", "prompted", "refused"):
        assert f"event={event}" in text
    for word in ("tablets", "umbrella", "Sunrise", SAM):
        assert word not in text



def test_forget_user_also_erases_the_days_and_lines_and_blanks_the_actor(store):
    r = run(rs.create_routine(SAM, "Morning", "waking", steps=(("tablets", "medicine"), "stretch")))
    run(rs.day_copy(SAM, r["id"]))
    run(rs.add_one_off(SAM, r["id"], TOMORROW, "take the drill"))
    fam = run(rs.create_routine(ALEX, "House", "dinner", visibility="family", steps=("feed the dog",)))
    run(rs.act_tapped(SAM, _first(fam, SAM), "done", verified=True))
    keep = _mk(owner=RILEY)
    assert run(rs.forget_user(SAM)) == 1
    for t in ("routines", "routine_days"):
        assert store.q(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (SAM,)) == [(0,)]
    assert store.q("SELECT COUNT(*) FROM routine_steps WHERE routine_id = ?", (r["id"],)) == [(0,)]
    assert store.q("SELECT COUNT(*) FROM routine_day_steps WHERE day_id LIKE ?", (r["id"] + "%",)) == [(0,)]
    assert store.q("SELECT COUNT(*) FROM routine_day_steps WHERE actor_user_id = ?", (SAM,)) == [(0,)]
    assert store.q("SELECT state FROM routine_day_steps WHERE day_id LIKE ?", (fam["id"] + "%",)) == [("done",)]  # Alex's stays
    assert run(rs.get_routine(RILEY, keep["id"])) is not None                      # control: nobody else's routine went



def test_erase_entity_also_deletes_day_lines_that_name_the_entity(store):
    named = run(rs.create_routine(ALEX, "Quinn visit", "window"))
    r = _mk(steps=("call Quinn", "bins"))
    run(rs.day_copy(ALEX, r["id"]))
    run(rs.add_one_off(ALEX, r["id"], TOMORROW, "take Quinn's book"))
    rx = re.compile(r"\bquinn\b", re.I)
    assert run(rs.erase_entity(ALEX, rx, strict=True)) >= 4
    assert run(rs.get_routine(ALEX, named["id"])) is None
    left = " ".join(t for (t,) in store.q("SELECT text FROM routine_steps") + store.q("SELECT text FROM routine_day_steps"))
    assert "Quinn" not in left and "bins" in left                                  # control: the other line stays




# -- the day under the one gate (1b) ------------------------------------------------------------------------------------------------
def test_an_entity_erase_between_the_template_read_and_the_copy_leaves_no_line_naming_it(store, monkeypatch):
    """The snapshot reads the template, then inserts the day lines. Old code: the erase did not share the snapshot's gate, ran whole
    in that gap (template step deleted, nothing on the day yet), and the copy then inserted the text it had already read."""
    r = _mk(steps=("call Quinn", "bins"))
    rx = re.compile(r"\bquinn\b", re.I)
    real, task = rs._steps, []

    async def steps(db, rid):
        out = await real(db, rid)
        if not task:
            async def forget():
                await memory_forgotten.add(ALEX, "Quinn")
                await rs.erase_entity(ALEX, rx, strict=True)
            task.append(asyncio.ensure_future(forget()))
            for _ in range(60):
                await asyncio.sleep(0)
        return out
    monkeypatch.setattr(rs, "_steps", steps)

    async def scenario():
        await rs.day_copy(ALEX, r["id"])
        await task[0]
    run(scenario())
    for table, col in (("routine_steps", "text"), ("routine_day_steps", "text")):
        assert not [t for (t,) in store.q(f"SELECT {col} FROM {table}") if rx.search(t)], table
    assert [t for (t,) in store.q("SELECT text FROM routine_day_steps")] == ["bins"]


def test_an_actors_own_delete_between_their_tick_and_its_write_writes_nothing_of_theirs(store, monkeypatch):
    """Sam ticks Alex's family routine; Sam's member delete runs between the tick's read and its write. Old code took only Alex's
    gate, so the pending tick wrote Sam's id back after Sam's erase had blanked it."""
    fam = run(rs.create_routine(ALEX, "House", "dinner", visibility="family", steps=("feed the dog",)))
    ds = _first(fam, SAM)

    async def scenario():
        _hook_rows(monkeypatch, "SELECT ds.state", lambda: rs.forget_user(SAM))
        with pytest.raises(rs.StoreError) as e:
            await rs.act_tapped(SAM, ds, "done", verified=True)
        assert e.value.code == "erased"
    run(scenario())
    assert store.q("SELECT state, actor_user_id FROM routine_day_steps WHERE id = ?", (ds,)) == [("pending", None)]
    assert _count(store, "routine_day_steps", "actor_user_id = ?", (SAM,)) == 0


def test_a_closed_day_is_settled_nothing_on_it_changes(store, monkeypatch):
    r = _mk()
    ds = _first(r)
    monkeypatch.setattr(rs, "_now_utc", lambda: THU + timedelta(days=1))
    run(rs.day_copy(ALEX, r["id"]))                                                # opening tomorrow closes today
    for action in ("done", "skip", "snooze", "undo"):
        with pytest.raises(rs.StoreError) as e:
            run(rs.act_tapped(ALEX, ds, action, verified=True))
        assert e.value.code == "day_closed"
    assert store.q("SELECT state FROM routine_day_steps WHERE id = ?", (ds,)) == [("pending",)]
    assert run(rs.act_tapped(ALEX, _first(r), "done", verified=True))["state"] == "done"   # control: the open day still ticks


def test_a_walk_named_yesterday_ticks_nothing_today(store, monkeypatch):
    r = _mk()
    ds = _first(r)
    run(rs.note_named("s1", ALEX, ds))
    t0 = rs._mono()
    monkeypatch.setattr(rs, "_mono", lambda: t0 + 5)                               # well inside WALK_S ...
    monkeypatch.setattr(rs, "_now_utc", lambda: THU + timedelta(hours=17))         # ... but past household midnight
    assert rs.named_step("s1", ALEX) is None
    with pytest.raises(rs.StoreError):
        run(rs.act_said("s1", ALEX, "done", verified=True))


def test_a_one_off_naming_a_forgotten_entity_is_refused(store):
    r = _mk()
    _forget(ALEX, "Quinn")
    with pytest.raises(rs.StoreError) as e:
        run(rs.add_one_off(ALEX, r["id"], TOMORROW, "take Quinn's book"))
    assert e.value.code == "names_forgotten"
    assert run(rs.add_one_off(ALEX, r["id"], TOMORROW, "take the book"))["text"] == "take the book"


def test_an_actor_erased_by_another_process_mid_tick_writes_nothing_of_theirs(store, monkeypatch):
    """The actor case across PROCESSES: Sam's delete runs elsewhere (its watermark first, then its eraser) between Sam's tick's read
    and its write. This process's gate sees nothing; the durable watermark re-read under the lock refuses the tick."""
    import erase_watermark

    fam = run(rs.create_routine(ALEX, "House", "dinner", visibility="family", steps=("feed the dog",)))
    ds = _first(fam, SAM)
    _as_another_process(monkeypatch)

    async def delete_sam_elsewhere():
        await erase_watermark.record(SAM)
        await rs.forget_user(SAM)

    async def scenario():
        _hook_rows(monkeypatch, "SELECT ds.state", delete_sam_elsewhere)
        with pytest.raises(rs.StoreError) as e:
            await rs.act_tapped(SAM, ds, "done", verified=True)
        assert e.value.code == "erased"
    run(scenario())
    assert store.q("SELECT state, actor_user_id FROM routine_day_steps WHERE id = ?", (ds,)) == [("pending", None)]
