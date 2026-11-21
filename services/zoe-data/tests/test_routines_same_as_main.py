"""Routines: ONE module writes the four tables, and nothing its entry points do has changed against ``main``.

``tests/fixtures/routines_as_on_main.json`` is the record of ``main`` (9ecea063c), made by running
``tests/routines_scenarios.py`` against an export of that commit under its own test configuration, never by this branch's
code: for each scenario, what every call returned or refused and the four tables as they stand at the end. The same
scenarios run here and must give the same record.

Covered (36): creating a routine (own, for a child by an adult, family, a guest, a family routine with a medicine line);
adding a template step and a one-off as the subject, another member, the child subject of a routine an adult set up, the adult
creator, a family member, a guest, for a past day; tapping done / skip / snooze / undo as the subject, another member, the
child, the adult creator, the bare panel on a family and on a personal routine, a medicine line unverified, verified and by
someone else; the said tick (the one step just named, then nothing named, then another member); a member erase; and the
conversation tier's walk ("what's next?", "done", "skip", "yep"), a bare "yep", a one-off against a timed reminder, another
member.

The one-writer scan is over every module of the service, no file exempt by name: a statement that writes ``routines``,
``routine_steps``, ``routine_days`` or ``routine_day_steps`` may appear only in ``routines_store.py``.

Negative controls run (each broke the code, saw the named test fail, restored): a medicine line ticked unverified ->
``tap_medicine_unverified_refused``; the family fridge rule dropped -> ``tap_family_by_the_panel``; a one-off allowed for a
past day -> ``one_off_past_day_refused``; the said tick's refusal code changed -> ``said_with_nothing_named``; an ``UPDATE
routine_day_steps`` planted in ``routines_tier.py`` -> the one-writer test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import routines_scenarios as scenarios
import sql_scan

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]
ON_MAIN = json.loads((Path(__file__).parent / "fixtures" / "routines_as_on_main.json").read_text("utf-8"))
TABLES = ("routines", "routine_steps", "routine_days", "routine_day_steps")
DOOR = "routines_store.py"


@pytest.fixture(scope="module")
def here():
    return scenarios.run_all()


def test_the_fixture_covers_every_scenario():
    assert set(ON_MAIN) == set(scenarios.scenarios()) and len(ON_MAIN) >= 36
    for prefix, least in (("create_", 5), ("add_step_", 6), ("one_off_", 5), ("tap_", 12), ("said_", 2), ("tier_", 4)):
        assert sum(name.startswith(prefix) for name in ON_MAIN) >= least, prefix


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_routines_entry_point_does_exactly_what_it_did_on_main(here, name):
    assert here[name] == ON_MAIN[name]


def test_the_record_shows_the_rows_and_the_refusals_not_only_the_replies(here):
    """The comparison has something to bite on: a tick's record holds the day line with its state, who acted and whether the
    voice was verified; a refusal holds its code; the tier's record holds Zoe's words."""
    done = [e for e in here["tap_medicine_verified"] if e[0] == "routine_day_steps"][0]
    assert done[5:] == ["take the tablets", "medicine", 1, "done", done[9], scenarios.ALEX, 1, 0]
    assert ["tap", "refused", "unverified", 403] in here["tap_medicine_unverified_refused"] or \
        any(e[:3] == ["tap", "refused", "unverified"] for e in here["tap_medicine_unverified_refused"])
    assert any(e[:3] == ["tap", "refused", "not_found"] for e in here["tap_other_refused"])
    assert [e[2] for e in here["tier_walk"] if e[0] == "say"][:2] == ["Next: brush teeth.", "Ticked. Next: pack the bag."]
    assert not [e for e in here["forget_user"] if e[0] == "routines" and scenarios.ALEX in e]


def _modules() -> dict:
    out = {}
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "scripts/", "alembic/")) or "/tests/" in rel or ".venv" in rel:
            continue
        out[rel] = path
    return out


def test_only_the_routines_store_writes_the_four_tables():
    modules = _modules()
    assert len(modules) > 100 and {DOOR, "routines_tier.py", "memory_service.py", "memory_forget_cascade.py", "fast_tiers.py"} <= set(modules)
    pattern = sql_scan.write_pattern(*TABLES)
    offenders = {rel: sql_scan.writes(path.read_text("utf-8"), pattern) for rel, path in modules.items() if rel != DOOR}
    assert not {rel: found for rel, found in offenders.items() if found}
    assert len(set(sql_scan.writes(modules[DOOR].read_text("utf-8"), pattern))) >= 12        # the scan sees the door's own writes


def test_the_scan_sees_each_way_a_write_can_be_spelled():
    for table in TABLES:
        pattern = sql_scan.write_pattern(table)
        for sql in (f"'INSERT INTO {table} (id) VALUES (?)'", f"'UPDATE {table} SET x = 1'", f"'DELETE FROM {table} WHERE id = ?'",
                    f"f'UPDATE {{schema}}.{table} SET x = 1'", f"'insert or replace into {table} (id) values (?)'"):
            assert sql_scan.writes(f"async def f(db, schema):\n    await db.execute({sql})\n", pattern), sql
        for planted in sql_scan.planted_forms(table):
            assert sql_scan.writes(planted, pattern), planted
    assert not sql_scan.writes("async def f(db):\n    await db.execute('SELECT * FROM routines')\n"
                               "    await db.execute('UPDATE routines_archive SET x = 1')\n", sql_scan.write_pattern("routines"))
