"""Routines: ONE module writes the four tables, and nothing its entry points do has changed against ``main``.

``tests/fixtures/routines_as_on_main.json`` is the record of ``main`` (c1a350458), made by running
``tests/routines_scenarios.py`` against an export of that commit under its own test configuration (HOME and every store path
in a scratch directory), never by this branch's code: for each scenario, AFTER EVERY CALL, what it returned or refused, the four
tables (every column of every row, read from the schema, not listed), what the routines modules logged, and after a spoken
turn the walk state. The clocks and the minted ids are frozen, nothing is masked. The same scenarios run here and must give the
same record.

Covered (49): creating a routine (own, for a child by an adult, family, a guest, a family routine with a medicine line);
adding a template step and a one-off as the subject, another member, the child subject of a routine an adult set up, the adult
creator, a family member, a guest, for a past day; tapping done / skip / snooze / undo as the subject, another member, the
child, the adult creator, the bare panel on a family and on a personal routine, a medicine line unverified, verified and by
someone else; the said tick (the one step just named, then nothing named, then another member); the readers as each viewer; a
member erase, an entity erase directly and through the forget cascade's own routines step; and the conversation tier, every
path through its own code: the walk ("what's next?", "done", "skip", "yep"), "start my ... routine" (and with nothing left),
the spoken undo, "I'm off" with one take-with line and with several, a medicine line by voice unverified and verified, "what's
left?" / "later" / "stop", a walk another tier ended, chat, the shadow mode, a bare "yep", a one-off against a timed reminder,
another member.

The one-writer scan is over every module of the service, its ``scripts/`` and ``alembic/`` included, and the repository's
``scripts/``, no file or directory exempt: a statement that writes ``routines``, ``routine_steps``, ``routine_days`` or
``routine_day_steps`` may appear only in ``routines_store.py`` (or, if ever legitimate, in ``ALLOWED`` by module, function and
exact count).

Negative controls run (each broke the code, saw the named test fail, restored): a medicine line ticked unverified ->
``tap_medicine_unverified_refused``; the family fridge rule dropped -> ``tap_family_by_the_panel``; a one-off allowed for a
past day -> ``one_off_past_day_refused``; the said tick's refusal code changed -> ``said_with_nothing_named``; an ``UPDATE
routine_day_steps`` planted in ``routines_tier.py`` -> the one-writer test. Round 3 (each through the tier's own code): ``_start``'s
reply words -> ``tier_start_done_then_spoken_undo``; ``_start`` reading tomorrow's day copy -> ``tier_start_when_all_done``;
``_leaving`` taking every kind of line, and ``_speak_many`` naming a line without ``note_named`` -> ``tier_leaving_one_line``;
the spoken undo's reply -> ``tier_start_done_then_spoken_undo``; the unverified medicine reply not re-naming the line ->
``tier_medicine_spoken``; chat no longer counted as verified -> ``tier_chat_walk``; ``turn_begins`` never closing the walk ->
``tier_walk_ends_when_another_tier_answers``; the walk entry's clock moved (in-memory state only, same reply) and a store log
event renamed -> ``tier_walk``; the shadow claim inverted for "start" -> ``tier_shadow_writes_nothing``; a guest shown a personal
routine -> ``readers_by_viewer``.
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
_OWNERS: dict = {}


def _owner(path: Path, line: int) -> str:
    """The OUTERMOST function (or method) of the module holding ``line``: the name ``door_callers.entry_points`` gives a call site,
    so a call made from a closure (the forget cascade's ``routines_`` inside ``_clear``) is matched to its site."""
    import ast

    import door_callers

    if path not in _OWNERS:
        tree = ast.parse(path.read_text("utf-8"))
        _OWNERS[path] = [(fn.lineno, fn.end_lineno, name) for name, fn in door_callers._top_functions(tree)]
    return next((name.split(".")[-1] for a, b, name in _OWNERS[path] if a <= line <= b), "<module>")


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
            path = Path(frame.f_code.co_filename).resolve()
            try:
                where = path.relative_to(ROOT).as_posix()
            except ValueError:
                where = "<elsewhere>"
            REACHED.setdefault(_name, set()).add((where, _owner(path, frame.f_lineno) if where != "<elsewhere>" else frame.f_code.co_name))
            return _real(*a, **k)

        setattr(routines_store, name, functools.wraps(real)(spy))
    try:
        return scenarios.run_all()
    finally:
        for name, real in kept.items():
            setattr(routines_store, name, real)


#: every (module, function, writing function of ``routines_store``) the CODE holds that the scenarios RUN through that function
#: is found by the spy; these are the ones they do NOT run, each with why. Exact, so the list cannot grow unseen. Every tier path
#: and the forget cascade's routines step ARE run (each through its own code); only the whole-member erase and the card's API are not.
_DELETE_USER = ("the whole-member erase (MemoryService.delete_user) runs some thirty stores before this one and needs the memory "
                "service's own database: its routines call is the store's forget_user, recorded by the forget_user scenario")
_CARD_API = "the card's API, new with this PR: main has no such route to compare with; pinned by tests/test_routines_card_api.py"
SITES_NOT_RUN = {
    ("memory_service.py", "delete_user", "forget_user"): _DELETE_USER,
    ("routers/routines.py", "act", "act_tapped"): _CARD_API, ("routers/routines.py", "today", "day_copy"): _CARD_API,
}


def test_the_fixture_covers_every_scenario(here):
    """Coverage is read from the code and from what RAN, not counted by name prefix. The store's writing functions are read
    from its code; every public one is reached by a scenario (``undo_said`` and ``erase_entity`` were not: each now has one).
    Every function elsewhere that names one is either run by a scenario (the spy saw that function make the call) or listed in
    ``SITES_NOT_RUN`` with its reason; a new caller fails here until a scenario runs it or it is listed.
    The tier's call sites are no longer exempt: each is RUN by a scenario through the tier's own code (``_start``, ``_leaving``,
    ``_speak_many``, the spoken undo and the unverified re-name in ``_bare``), and so is the forget cascade's routines step; a
    call made from a closure is matched to the outermost function holding it, as ``door_callers`` names the site.
    Controls run: a new writing function ``async def sneak(): await _delete_routines(None, [])`` in ``routines_store.py`` fails
    this naming it (never reached); a new ``async def sneak(): await routines_store.undo_said("s", "m", verified=True)`` in
    ``routines_tier.py`` fails it as a caller neither run nor listed; the spy keyed on the calling frame's own name again (no
    outermost-function mapping) fails it with the cascade's ``_clear`` site not run; the cascade's ``want("routines")`` misspelt
    (its routines step never runs) fails it the same way."""
    import door_callers

    assert set(ON_MAIN) == set(scenarios.scenarios()) and len(ON_MAIN) >= 49
    writers = store_writers()
    public = {w for w in writers if not w.startswith("_")}
    assert len(public) >= 10 and sorted(public - set(REACHED)) == []
    sites = {(module, fn.split(".")[-1], door) for (module, fn), doors in door_callers.entry_points(ROOT, "routines_store", writers).items()
             for door in doors}
    not_run = {site for site in sites if (site[0], site[1]) not in REACHED.get(site[2], set())}
    assert not_run == set(SITES_NOT_RUN), {"not run, not listed": sorted(not_run - set(SITES_NOT_RUN)),
                                           "listed, but run or gone": sorted(set(SITES_NOT_RUN) - not_run)}
    assert len(sites - not_run) >= 13                              # ... and the tier's paths and the cascade's step ARE run
    tier = {site for site in sites if site[0] == "routines_tier.py"}
    assert len(tier) >= 12 and not tier & not_run                  # every call site of the tier, by execution


@pytest.mark.parametrize("name", sorted(ON_MAIN))
def test_every_routines_entry_point_does_exactly_what_it_did_on_main(here, name):
    assert here[name] == ON_MAIN[name]


def _end(record: list, table: str) -> list:
    """The rows of ``table`` at the end of a scenario (``[table, *row]``)."""
    ends = [e for e in record if e[0] == "rows at the end"]
    assert len(ends) == 1
    return [row for row in ends[0][1] if row[0] == table]


def _replies(record: list) -> list:
    return [e[2] for e in record if e[0] == "say"]


def test_the_record_shows_the_rows_and_the_refusals_not_only_the_replies(here):
    """The comparison has something to bite on: a tick's record holds the WHOLE day line (every column, read from the schema:
    its state, when a snooze ends, who acted, whether the voice was verified, how it was made); a refusal holds its code; the
    tier's record holds Zoe's words. Controls run: the snooze length written changed (``SNOOZE_MIN`` 15 -> 16) ->
    ``tap_snooze_subject`` and ``tap_done_undo_snooze_done`` fail; the one-off's ``created_via`` written as ``"spoken"`` instead
    of ``"voice"`` -> ``tier_one_off_and_timed_reminder`` fails (neither column was recorded before)."""
    done = [e for e in _end(here["tap_medicine_verified"], "routine_day_steps")][0]
    assert done[5:14] == ["take the tablets", "medicine", 1, "done", None, "tapped", scenarios.ALEX, 1, 0]
    snoozed = _end(here["tap_snooze_subject"], "routine_day_steps")[0]                        # every column: snooze_until ...
    assert snoozed[8:10] == ["snoozed", "2026-11-12T07:45:00+00:00"]
    one_off = [e for e in _end(here["tier_one_off_and_timed_reminder"], "routine_day_steps") if e[4] == "one_off"][0]  # ... created_via
    assert one_off[15] == "voice" and len(one_off) == 1 + 17
    assert ["tap", "refused", "unverified", 403] in here["tap_medicine_unverified_refused"]
    assert any(e[:3] == ["tap", "refused", "not_found"] for e in here["tap_other_refused"])
    assert _replies(here["tier_walk"])[:2] == ["Next: brush teeth.", "Ticked. Next: pack the bag."]
    kept = _end(here["forget_user"], "routines")
    assert kept and all(row[1:].count(scenarios.ALEX) == 0 for row in kept)                 # Riley's stays, with no trace of Alex


def test_rows_are_recorded_after_every_call_not_only_at_the_end(here):
    """Every call of every scenario is followed by the four tables as they then stood (finding: in ``done -> undo -> snooze ->
    done`` a wrong value written by the undo was overwritten before the only record). Control run: the undo writing
    ``actor_user_id = NULL`` (``None if _guest(actor) else actor`` -> ``None``) only on the undo -> ``tap_done_undo_snooze_done``
    fails although the end state is the same."""
    for name, record in here.items():
        calls = sum(1 for e in record if e[0] in ("say", "day_copy") or (len(e) > 1 and e[1] in ("returned", "refused")))
        assert calls and sum(1 for e in record if e[0] == "rows") >= calls, name
    steps = here["tap_done_undo_snooze_done"]
    after = [steps[i + 1] for i, e in enumerate(steps) if e[0] == "tap"]
    assert len(after) == 4 and all(e[0] == "rows" and e[1] != "unchanged" for e in after)
    undone = [row for row in after[1][1] if row[0] == "routine_day_steps"][0]
    assert undone[8] == "pending" and undone[11] == scenarios.ALEX             # the undo's own row, before the snooze overwrote it


def test_no_scenario_stops_short_or_fails_inside_the_tier(here):
    """A fixture captured on a broken path would pin the breakage: every scenario reaches its end (no "no day copy"), nothing the
    routines modules logged is a warning or an error (the tier swallows its exceptions: its failure is only a log line), and each
    tier path answered the way its scenario says (so main's record is of the path, not of a step-aside). Control run: ``_summary``
    raising inside the tier (swallowed, logged as a warning) fails this."""
    for name, record in here.items():
        assert ["no day copy"] not in record, name
        assert not [e for e in record if e[0] == "logged" and e[2] not in ("INFO", "DEBUG")], name
    assert _replies(here["tier_start_done_then_spoken_undo"]) == [
        "Morning: 2 things. First, brush teeth.", "Ticked. Next: pack the bag.", "OK, it's back on the list: brush teeth.", None, None,
        "Morning: 2 things. First, brush teeth."]
    assert _replies(here["tier_start_when_all_done"]) == ["That's your morning routine done."]
    assert _replies(here["tier_leaving_one_line"])[1:] == ["Before you go: take the drill.", "Ticked. Next: brush teeth."]
    assert _replies(here["tier_leaving_several_lines"])[2:] == ["Before you go: take the drill and pack the umbrella.",
                                                                "Which one: take the drill or pack the umbrella?", None]
    medicine = _replies(here["tier_medicine_spoken"])
    assert medicine[1].startswith("I can only tick take the tablets") and medicine[2] == "Ticked. Next: pack the bag."
    assert _replies(here["tier_whats_left_snooze_stop"])[0].startswith("3 things left") and "OK." in _replies(here["tier_whats_left_snooze_stop"])
    assert _replies(here["tier_walk_ends_when_another_tier_answers"]) == ["Next: brush teeth.", None]
    assert _replies(here["tier_chat_walk"])[:2] == ["Next: brush teeth.", "Ticked. Next: pack the bag."]
    assert set(_replies(here["tier_shadow_writes_nothing"])) == {None}
    assert any("shadow would=next" in e[3] for e in here["tier_shadow_writes_nothing"] if e[0] == "logged")
    cascade = here["erase_entity_by_the_forget_cascade"]
    own = [row[4] for row in _end(cascade, "routine_steps") if row[2] == "<id1>"]              # Alex's routine (its first id)
    assert ["cascade failed", []] in cascade and own == ["pack the bag"]                     # "teeth" went from Alex's routine only
    assert [row for row in _end(cascade, "routine_steps") if row[2] != "<id1>" and "brush teeth" in row]


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
