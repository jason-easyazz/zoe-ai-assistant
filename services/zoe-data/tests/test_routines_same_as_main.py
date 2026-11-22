"""Routines: ONE module writes the four tables, and nothing its entry points do has changed against ``main``.

``tests/fixtures/routines_as_on_main.json`` is the record of ``main`` (b2bbe84ca), made by running
``tests/routines_scenarios.py`` against an export of that commit under its own test configuration, never by this branch's
code: for each scenario, what every call returned or refused and the four tables as they stand at the end, every column
of every row (read from the schema, not listed). The same
scenarios run here and must give the same record.

Covered (36): creating a routine (own, for a child by an adult, family, a guest, a family routine with a medicine line);
adding a template step and a one-off as the subject, another member, the child subject of a routine an adult set up, the adult
creator, a family member, a guest, for a past day; tapping done / skip / snooze / undo as the subject, another member, the
child, the adult creator, the bare panel on a family and on a personal routine, a medicine line unverified, verified and by
someone else; the said tick (the one step just named, then nothing named, then another member); a member erase; and the
conversation tier's walk ("what's next?", "done", "skip", "yep"), a bare "yep", a one-off against a timed reminder, another
member.

The one-writer scan is over every module of the service, its ``scripts/`` and ``alembic/`` included, and the repository's
``scripts/``, no file or directory exempt: a statement that writes ``routines``, ``routine_steps``, ``routine_days`` or
``routine_day_steps`` may appear only in ``routines_store.py`` (or, if ever legitimate, in ``ALLOWED`` by module, function and
exact count).

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


#: what the scenarios REACHED in ``routines_store`` while ``here`` ran them: {writing function: {(module, function) that called it}}
REACHED: dict = {}


def store_writers() -> set:
    """The functions of ``routines_store`` that write a routines table (directly, or through another that does), read from its code."""
    import door_callers

    door = (ROOT / DOOR).read_text("utf-8")
    return door_callers.door_functions(door, door_callers.direct_sql_writers(door, sql_scan.write_pattern(*TABLES)))


@pytest.fixture(scope="module")
def here():
    """The scenarios' record, made with a spy on every writing function of the store that only NOTES the call and who made it
    (it passes through untouched, so the record is the same with or without it: the comparison with main would fail otherwise)."""
    import functools
    import sys

    import routines_store

    kept = {}
    for name in store_writers():
        real = kept[name] = getattr(routines_store, name)

        def spy(*a, _real=real, _name=name, **k):
            frame = sys._getframe(1)
            try:
                where = Path(frame.f_code.co_filename).resolve().relative_to(ROOT).as_posix()
            except ValueError:
                where = "<elsewhere>"
            REACHED.setdefault(_name, set()).add((where, frame.f_code.co_name))
            return _real(*a, **k)

        setattr(routines_store, name, functools.wraps(real)(spy))
    try:
        return scenarios.run_all()
    finally:
        for name, real in kept.items():
            setattr(routines_store, name, real)


_TIER = "routines_tier.py"
#: every (module, function, writing function of ``routines_store``) the CODE holds that the scenarios RUN through that function
#: is found by the spy; these are the ones they do NOT run, each with why. Exact, so the list cannot grow unseen.
_ERASER = "an eraser's call: the store function itself is recorded (forget_user / erase_entity_own_routines_only), the eraser around it is not"
_TIER_GAP = "A TIER PATH WITH NO SCENARIO YET (found in review by reading the code; the store function it calls is recorded)"
SITES_NOT_RUN = {
    ("memory_service.py", "delete_user", "forget_user"): _ERASER,
    ("memory_forget_cascade.py", "_clear", "erase_entity"): _ERASER,
    (_TIER, "_bare", "note_named"): _TIER_GAP, (_TIER, "_bare", "undo_said"): _TIER_GAP, (_TIER, "_leaving", "day_copy"): _TIER_GAP,
    (_TIER, "_speak_many", "note_named"): _TIER_GAP, (_TIER, "_start", "day_copy"): _TIER_GAP, (_TIER, "_start", "note_named"): _TIER_GAP,
}


def test_the_fixture_covers_every_scenario(here):
    """Coverage is read from the code and from what RAN, not counted by name prefix. The store's writing functions are read
    from its code; every public one is reached by a scenario (``undo_said`` and ``erase_entity`` were not: each now has one).
    Every function elsewhere that names one is either run by a scenario (the spy saw that function make the call) or listed in
    ``SITES_NOT_RUN`` with its reason; a new caller fails here until a scenario runs it or it is listed.
    Controls run: a new writing function ``async def sneak(): await _delete_routines(None, [])`` in ``routines_store.py`` fails
    this naming it (never reached); a new ``async def sneak(): await routines_store.undo_said("s", "m", verified=True)`` in
    ``routines_tier.py`` fails it as a caller neither run nor listed."""
    import door_callers

    assert set(ON_MAIN) == set(scenarios.scenarios()) and len(ON_MAIN) >= 38
    writers = store_writers()
    public = {w for w in writers if not w.startswith("_")}
    assert len(public) >= 10 and sorted(public - set(REACHED)) == []
    sites = {(module, fn.split(".")[-1], door) for (module, fn), doors in door_callers.entry_points(ROOT, "routines_store", writers).items()
             for door in doors}
    not_run = {site for site in sites if (site[0], site[1]) not in REACHED.get(site[2], set())}
    assert not_run == set(SITES_NOT_RUN), {"not run, not listed": sorted(not_run - set(SITES_NOT_RUN)),
                                           "listed, but run or gone": sorted(set(SITES_NOT_RUN) - not_run)}
    assert len(sites - not_run) >= 6                               # ... and the tier's main paths ARE run


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_routines_entry_point_does_exactly_what_it_did_on_main(here, name):
    assert here[name] == ON_MAIN[name]


def test_the_record_shows_the_rows_and_the_refusals_not_only_the_replies(here):
    """The comparison has something to bite on: a tick's record holds the WHOLE day line (every column, read from the schema:
    its state, when a snooze ends, who acted, whether the voice was verified, how it was made); a refusal holds its code; the
    tier's record holds Zoe's words. Controls run: the snooze length written changed (``SNOOZE_MIN`` 15 -> 16) ->
    ``tap_snooze_subject`` and ``tap_done_undo_snooze_done`` fail; the one-off's ``created_via`` written as ``"spoken"`` instead
    of ``"voice"`` -> ``tier_one_off_and_timed_reminder`` fails (neither column was recorded before)."""
    done = [e for e in here["tap_medicine_verified"] if e[0] == "routine_day_steps"][0]
    assert done[5:14] == ["take the tablets", "medicine", 1, "done", None, "tapped", scenarios.ALEX, 1, 0]
    snoozed = [e for e in here["tap_snooze_subject"] if e[0] == "routine_day_steps"][0]       # every column: snooze_until ...
    assert snoozed[8:10] == ["snoozed", "2026-11-12T07:45:00+00:00"]
    one_off = [e for e in here["tier_one_off_and_timed_reminder"] if e[0] == "routine_day_steps"][0]   # ... and created_via
    assert one_off[4] == "one_off" and one_off[15] == "voice" and len(one_off) == 1 + 17
    assert ["tap", "refused", "unverified", 403] in here["tap_medicine_unverified_refused"] or \
        any(e[:3] == ["tap", "refused", "unverified"] for e in here["tap_medicine_unverified_refused"])
    assert any(e[:3] == ["tap", "refused", "not_found"] for e in here["tap_other_refused"])
    assert [e[2] for e in here["tier_walk"] if e[0] == "say"][:2] == ["Next: brush teeth.", "Ticked. Next: pack the bag."]
    assert not [e for e in here["forget_user"] if e[0] == "routines" and scenarios.ALEX in e]


REPO = ROOT.parents[1]
#: the scripts and migrations scanned with the service: the service's own ``scripts/`` and ``alembic/`` and the repository's
#: ``scripts/`` (a maintenance script is as much a second writer as a module of the service).
OUTSIDE = (ROOT / "scripts", ROOT / "alembic", REPO / "scripts")
#: a write to a routines table outside the store that is LEGITIMATE, as {(module, function): exact number of distinct writes}.
#: None today (``alembic/versions/0081_routines.py`` creates and drops the tables, it writes no row); a data migration that
#: has to backfill one is listed here by its module and function, with its count, so a second write beside it still fails.
ALLOWED: dict = {}


def _modules() -> dict:
    """Every Python module of the service (its ``scripts/`` and ``alembic/`` included) and of the repository's ``scripts/``,
    keyed by path from the repository root's view of the service (``alembic/...``) or the repository (``<repo>/scripts/...``)."""
    out = {}
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tests/") or "/tests/" in rel or ".venv" in rel or "__pycache__" in rel:
            continue
        out[rel] = path
    for path in sorted((REPO / "scripts").rglob("*.py")):
        if "__pycache__" not in path.parts and ".venv" not in path.parts:
            out["<repo>/" + path.relative_to(REPO).as_posix()] = path
    return out


def _writes_by_function(source: str, pattern) -> dict:
    """{function (dotted, ``<module>`` for top level): number of distinct writes in it}. A module that does not parse (an old
    archived script) is searched as raw text instead, so it cannot hide a write by being broken."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        found = {" ".join(m.group(0).split()) for m in pattern.finditer(source)}
        return {"<unparsed>": len(found)} if found else {}
    out: dict = {}

    def walk(node, where):              # every SQL text belongs to the innermost function (or class) around it
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, child.name if where == "<module>" else f"{where}.{child.name}")
            elif isinstance(child, ast.expr):
                for text in sql_scan.texts(ast.Expression(child)):
                    if pattern.search(text):
                        out.setdefault(where, set()).add(" ".join(text.split()))
            else:
                walk(child, where)

    walk(tree, "<module>")
    return {fn: len(found) for fn, found in out.items()}


def test_only_the_routines_store_writes_the_four_tables():
    """No module but the store writes a routines table: the service's modules, its ``scripts/`` and ``alembic/``, and the
    repository's ``scripts/`` (none exempt by directory; a legitimate one would be listed in ``ALLOWED`` by module, function
    and exact count). Controls run: an ``UPDATE routine_day_steps`` planted in ``routines_tier.py``; a ``DELETE FROM
    routine_days`` planted in ``alembic/versions/0081_routines.py``'s ``downgrade``; an ``INSERT INTO routine_steps`` planted in
    the repository's ``scripts/setup/populate_multica.py`` - each fails this."""
    modules = _modules()
    assert len(modules) > 100 and {DOOR, "routines_tier.py", "memory_service.py", "memory_forget_cascade.py", "fast_tiers.py",
                                   "alembic/versions/0081_routines.py", "<repo>/scripts/setup/populate_multica.py"} <= set(modules)
    assert sum(rel.startswith("alembic/") for rel in modules) >= 50 and sum(rel.startswith("<repo>/scripts/") for rel in modules) >= 100
    pattern = sql_scan.write_pattern(*TABLES)
    found = {(rel, fn): n for rel, path in modules.items() if rel != DOOR
             for fn, n in _writes_by_function(path.read_text("utf-8", errors="replace"), pattern).items()}
    assert found == ALLOWED
    assert len(set(sql_scan.writes(modules[DOOR].read_text("utf-8"), pattern))) >= 12        # the scan sees the door's own writes
    door = _writes_by_function(modules[DOOR].read_text("utf-8"), pattern)
    assert sum(door.values()) >= 12 and "<module>" not in door                                  # ... and places each in its function


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
