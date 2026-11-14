"""Routines stage 1, the store (``routines_store.py``, alembic 0082). Synthetic members only.

Every rule has a negative control beside it: the arm that proves the check can fail (another person, a guest, a family routine
asked to hold a medicine line, an inventory with the entries removed).
"""
from __future__ import annotations

import re
import sqlite3

import pytest

import forget_reach as fr
import routines_store as rs
from routines_support import ALEX, RILEY, SAM, migrate, run, store  # noqa: F401 - store is a fixture

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
def test_forget_user_erases_every_routine_of_the_subject_and_blanks_them_elsewhere(store):
    r = run(rs.create_routine(SAM, "Morning", "waking", steps=(("tablets", "medicine"), "stretch")))
    kid = run(rs.create_routine(RILEY, "School", "leaving", created_by=SAM, steps=("bag",)))   # Sam set it up for Riley
    keep = _mk()
    assert run(rs.forget_user(SAM)) == 1
    assert store.q("SELECT COUNT(*) FROM routines WHERE user_id = ?", (SAM,)) == [(0,)]
    assert store.q("SELECT COUNT(*) FROM routine_steps WHERE routine_id = ?", (r["id"],)) == [(0,)]
    assert store.q("SELECT created_by FROM routines WHERE id = ?", (kid["id"],)) == [(RILEY,)]
    assert run(rs.get_routine(ALEX, keep["id"])) is not None                       # control: nobody else's routine went
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


def test_erase_entity_deletes_routines_and_steps_that_name_the_entity(store):
    named = run(rs.create_routine(ALEX, "Quinn visit", "window"))
    r = _mk(steps=("call Quinn", "bins"))
    rx = re.compile(r"\bquinn\b", re.I)
    assert run(rs.erase_entity(ALEX, rx, strict=True)) == 2
    assert run(rs.get_routine(ALEX, named["id"])) is None
    assert [s["text"] for s in run(rs.get_routine(ALEX, r["id"]))["steps"]] == ["bins"]     # control: the other line stays
    assert run(rs.erase_entity(SAM, rx, strict=True)) == 0                                  # only the member's own


def test_the_forget_inventory_reaches_every_routine_table_and_would_catch_a_missing_entry():
    found = fr.discover_user_tables([rs.__file__.replace("routines_store.py", "alembic/versions/0082_routines.py")])
    assert TABLES == set(found)                                                     # the discovery sees all four (children too)
    assert not set(fr.uncovered_tables(found)) & TABLES
    assert all(fr.BY_KEY[k].status == fr.REACHED and fr.BY_KEY[k].member == "routines_store.forget_user" for k in TABLES)
    stripped = {s.key for s in fr.TABLES if s.key not in TABLES}                   # negative control: drop the entries
    assert set(found) - stripped == TABLES
