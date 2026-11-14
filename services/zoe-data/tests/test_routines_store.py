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
    monkeypatch.setattr(rs, "_mono", lambda: t0 + 5)
    out = run(rs.act_said("s1", ALEX, "done", verified=False))
    assert out["state"] == "done"
    assert store.q("SELECT state, known_by, actor_user_id, verified, prompts FROM routine_day_steps WHERE id = ?", (ds,)) == [
        ("done", "said", ALEX, 0, 1)]
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


