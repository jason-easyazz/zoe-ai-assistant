"""Alarms and timers each have ONE door. Structural, over EVERY module of the service (tests and migrations aside).

ALARMS live in Postgres (``alarms``, ``alarm_events``, ``alarm_overrides``) and only ``alarms.py`` writes them. The scan reads an
f-string whole (``x`` standing where a value is interpolated), allows any schema name and Postgres' ``ONLY``, and is not fooled by
the calendar's own ``event_alarms`` table.

TIMERS live in one in-process registry, ``skybridge_service._TimerStore`` (snapshotted to ``~/.zoe/timers.json``). Only that
class's own methods change it: nothing else reaches into ``_by_owner``, writes a field of a timer, or holds the store itself
(other modules call the public functions of ``skybridge_service``); a name bound to the store through any chain of rebindings is
followed.

Negative controls run on the real tree (then restored): ``UPDATE alarms SET label = ?`` planted in ``routers/alarms.py`` ->
``test_only_alarms_py_writes_the_alarm_tables`` fails naming it; ``t["announced"] = True`` put back in
``skybridge_service._ring_when_done`` -> ``test_only_the_timer_store_changes_a_timer`` fails naming that function;
``from skybridge_service import _timers`` planted in ``routers/skybridge.py`` -> the same test fails naming that file. The planted
cases below keep the scans themselves honest.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import sql_scan

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]

ALARM_TABLES = ("alarms", "alarm_events", "alarm_overrides")
ALARM_WRITE = sql_scan.write_pattern(*ALARM_TABLES)      # f-strings and concatenations whole, any schema, ONLY (tests/sql_scan.py)
TIMER_FIELDS = {"timer_id", "label", "duration_seconds", "set_seconds", "created_at", "expires_at", "panel_id", "announced", "voice_failed"}
STORE, STORE_CLASS, STORE_MODULE = "_timers", "_TimerStore", "skybridge_service"


def _modules() -> list:
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return sorted(p for p in ROOT.rglob("*.py") if not skip.intersection(p.relative_to(ROOT).parts))


def alarm_writes(source: str) -> list:
    return sql_scan.writes(source, ALARM_WRITE)


def test_only_alarms_py_writes_the_alarm_tables():
    modules = _modules()
    assert len(modules) > 100 and ROOT / "routers" / "alarms.py" in modules and ROOT / "alarm_voice.py" in modules
    offenders = {p.relative_to(ROOT).as_posix(): found for p in modules
                 if p.relative_to(ROOT).as_posix() != "alarms.py" and (found := alarm_writes(p.read_text("utf-8")))}   # (the
    # door is the top-level module; ``routers/alarms.py`` shares its file name and IS scanned: the control found that)
    assert not offenders, offenders
    assert len(alarm_writes((ROOT / "alarms.py").read_text("utf-8"))) >= 15      # the door itself holds the writes (the scan sees them)


def test_a_planted_alarm_write_is_caught():
    for sql in ("UPDATE alarms SET label = ?", "INSERT INTO alarm_events (id) VALUES (?)", "DELETE FROM alarm_overrides WHERE alarm_id = ?",
                'UPDATE "public"."alarms" SET x = ?', "UPDATE public1.alarms SET x = ?", "DELETE FROM ONLY alarms WHERE id = ?",
                "update only app_2$x.alarm_events set x = ?", "insert or replace into alarms (id) values (1)"):
        assert alarm_writes(f"async def f(db):\n    await db.execute('{sql}')\n"), sql
    for sql in ('f"UPDATE {schema}.alarms SET x = ?"', 'f"DELETE FROM {schema}.alarm_overrides WHERE {col} = ?"',
                "f\"UPDATE alarms SET {', '.join(sets)} WHERE id = ?\""):
        assert alarm_writes(f"async def f(db, schema, col, sets):\n    await db.execute({sql})\n"), sql
    for table in ALARM_TABLES:                    # a write split by ``+``, and Postgres' ONLY (control: ``sql_scan`` skipping ``+``)
        for planted in sql_scan.planted_forms(table):
            assert alarm_writes(planted), planted
    # the calendar's ``event_alarms`` is another table; a read is not a write
    assert not alarm_writes("async def f(db):\n    await db.execute('DELETE FROM event_alarms WHERE event_id = ?')\n"
                            "    await db.execute('SELECT * FROM alarms WHERE id = ?')\n    await db.execute('UPDATE alarms_archive SET x = 1')\n")


def _store_names(tree) -> set:
    """Every name this module can reach the timer store (or its class) by: the names themselves, ``from skybridge_service import
    _timers as X``, and any CHAIN of plain rebindings (``a = _timers``; ``b = a``; ``c = sky._timers``), followed to a fixed point."""
    names = {STORE, STORE_CLASS}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == STORE_MODULE:
            names |= {a.asname or a.name for a in node.names if a.name in (STORE, STORE_CLASS)}
    assigns = [n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None]
    while True:
        found = {t.id for n in assigns if getattr(n.value, "id", getattr(n.value, "attr", "")) in names
                 for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)} - names
        if not found:
            return names
        names |= found


def timer_offences(source: str, *, is_store_module: bool) -> list:
    """Where this module changes a timer, or holds the store, outside ``_TimerStore``: ``(what, where)`` pairs."""
    tree = ast.parse(source)
    inside = {id(n) for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef) and cls.name == STORE_CLASS for n in ast.walk(cls)}
    owner = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                owner.setdefault(id(n), fn.name)
    names = _store_names(tree)
    out = []
    for node in ast.walk(tree):
        if id(node) in inside:
            continue
        where = owner.get(id(node), "<module>")
        if isinstance(node, ast.Attribute) and node.attr in ("_by_owner", "_save", "_prune"):
            out.append((node.attr, where))                                   # the registry itself, or its snapshot, from outside
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) and isinstance(node.slice, ast.Constant) \
                and node.slice.value in TIMER_FIELDS and is_store_module:
            out.append((f"[{node.slice.value!r}] =", where))                 # a timer's field written outside the store
        elif not is_store_module:
            if isinstance(node, ast.Name) and node.id in names and isinstance(node.ctx, ast.Load):
                out.append((node.id, where))                                 # another module holding the store
            elif isinstance(node, ast.Attribute) and node.attr in (STORE, STORE_CLASS):
                out.append((node.attr, where))
            elif isinstance(node, ast.ImportFrom) and node.module == STORE_MODULE and any(a.name in (STORE, STORE_CLASS) for a in node.names):
                out.append(("import", where))
    return sorted(set(out))


def test_only_the_timer_store_changes_a_timer():
    modules = _modules()
    assert ROOT / "skybridge_service.py" in modules and ROOT / "routers" / "skybridge.py" in modules
    offenders = {p.relative_to(ROOT).as_posix(): found for p in modules
                 if (found := timer_offences(p.read_text("utf-8"),
                                             is_store_module=p.relative_to(ROOT).as_posix() == "skybridge_service.py"))}
    assert not offenders, offenders
    # the store module does define the store, and the class does hold the changes (the scan is looking at the right thing)
    tree = ast.parse((ROOT / "skybridge_service.py").read_text("utf-8"))
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == STORE_CLASS)
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert {"create", "adjust", "cancel_by_id", "cancel_any", "spoke", "_save", "_prune"} <= methods


def test_a_planted_timer_change_outside_the_store_is_caught():
    in_store_module = ("class _TimerStore:\n    def create(self):\n        self._by_owner = {}\n        t = {}\n        t['expires_at'] = 1\n"
                       "        self._save()\n_timers = _TimerStore()\n"
                       "async def ring(t):\n    t['announced'] = True\n"
                       "def reach():\n    return _timers._by_owner\n"
                       "def snapshot(store):\n    store._save()\n"
                       "def fine():\n    return _timers.create()\n")
    assert timer_offences(in_store_module, is_store_module=True) == [("['announced'] =", "ring"), ("_by_owner", "reach"), ("_save", "snapshot")]
    elsewhere = ("import skybridge_service as sky\nfrom skybridge_service import _timers as store, cancel_any_timer\n"
                 "mine = store\nagain = mine\n"
                 "def a():\n    return again.create('u', 'x', 1)\n"
                 "def b():\n    return sky._timers.cancel_any('t')\n"
                 "def fine():\n    return cancel_any_timer('t')\n")
    found = timer_offences(elsewhere, is_store_module=False)
    assert ("again", "a") in found and ("_timers", "b") in found and ("import", "<module>") in found
    assert not timer_offences("from skybridge_service import cancel_any_timer\ndef fine(t):\n    t['label'] = 'x'\n    return cancel_any_timer('t')\n",
                              is_store_module=False)
