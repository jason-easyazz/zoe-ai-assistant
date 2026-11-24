"""Alarms and timers each have ONE door. Structural, over EVERY module of the service (tests and migrations aside).

ALARMS live in Postgres (``alarms``, ``alarm_events``, ``alarm_overrides``) and only ``alarms.py`` writes them. The scan reads an
f-string whole (``x`` standing where a value is interpolated), allows any schema name and Postgres' ``ONLY``, and is not fooled by
the calendar's own ``event_alarms`` table.

TIMERS live in one in-process registry, ``skybridge_service._TimerStore`` (snapshotted to ``~/.zoe/timers.json``). Only that
class's own methods change it: nothing else reaches into its own state (``STORE_PRIVATE``), writes a field of a timer, or holds
the store itself (other modules call the public functions of ``skybridge_service``); a name bound to the store through any chain
of rebindings is followed. The timer-write checks run on EVERY module (``timer_offences`` says how closely): the store hands its
own timer dicts out (``handed_out``: ``cancel_any_timer`` / ``cancel_timer_by_id``), so a module that imports it is scanned as
the store module is, and a timer-only field written anywhere is a timer change. What is allowed outside, by exact count: ``ALLOWED``.

Negative controls run on the real tree (then restored): ``UPDATE alarms SET label = ?`` planted in ``routers/alarms.py`` ->
``test_only_alarms_py_writes_the_alarm_tables`` fails naming it; ``t["announced"] = True`` put back in
``skybridge_service._ring_when_done`` -> ``test_only_the_timer_store_changes_a_timer`` fails naming that function;
``from skybridge_service import _timers`` planted in ``routers/skybridge.py`` -> the same test fails naming that file. The planted
cases below keep the scans themselves honest.
Control run for the dict-method forms (review of #2688): ``t.update({'announced': True})`` planted in
``skybridge_service._ring_when_done`` -> ``test_only_the_timer_store_changes_a_timer`` fails naming it (it passed before: only
subscript writes were read). Round 3: the controls named in ``test_only_the_timer_store_changes_a_timer`` and
``test_a_timer_changed_in_any_other_module_is_caught``; and with ``_load`` / ``_rings`` dropped from ``STORE_PRIVATE`` the
planted reload in ``test_a_planted_timer_change_outside_the_store_is_caught`` escapes.
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
#: fields ONLY a timer has in this service: a write of one is a timer change in ANY module, whatever the name it is made through
TIMER_ONLY_FIELDS = {"announced", "voice_failed", "set_seconds"}
#: the store's own state, reached from outside the class: the registry, its snapshot, its reload, the ring tasks
STORE_PRIVATE = ("_by_owner", "_save", "_prune", "_load", "_write_snapshot", "_arm", "_disarm", "_rings")
#: what the scan reports outside the store that is allowed, each by module + function + what + EXACT count (a new one fails)
ALLOWED = {
    ("main.py", "_build_readiness_report", "['expires_at'] ="): (1, "the readiness report cache's own expiry stamp, not a timer"),
    ("skybridge_service.py", "_ring_when_done", "_rings"): (2, "a ring task removes ITSELF from the ring registry when it ends"),
    ("skybridge_service.py", "preload_timers", "_load"): (1, "startup reads the snapshot on a worker thread, before serving"),
}


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


DICT_WRITES = {"update", "pop", "setdefault", "clear", "popitem", "__setitem__", "__delitem__"}


def _is_field(node, fields=TIMER_FIELDS) -> bool:
    return isinstance(node, ast.Constant) and node.value in fields


def _hands_out(value, handouts) -> bool:
    """``value`` is (read from) the result of a function that hands out the store's OWN timer dict: ``cancel_any_timer(i)``,
    ``sky.cancel_timer_by_id(o, i)["label"]`` ..."""
    while isinstance(value, (ast.Subscript, ast.Attribute, ast.Call)):
        if isinstance(value, ast.Call):
            f = value.func
            if (isinstance(f, ast.Name) and f.id in handouts) or (isinstance(f, ast.Attribute) and f.attr in handouts):
                return True
            value = f
        else:
            value = value.value
    return False


def handed_out(source: str) -> set:
    """The functions of the store module that return the store's own timer dicts (not copies): a top-level function with a
    ``return`` of ``_timers.<method>(...)`` (or of a comprehension yielding the store's dicts as they are). READ FROM THE CODE,
    so a new one is followed into every module that calls it."""
    out = set()
    for fn in ast.parse(source).body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and node.value is not None:
                v = node.value
                if _root(v) == STORE or (isinstance(v, (ast.ListComp, ast.GeneratorExp, ast.SetComp)) and isinstance(v.elt, ast.Name)
                                         and any(_root(g.iter) == STORE and _targets(g.target) == [v.elt.id] for g in v.generators)):
                    out.add(fn.name)
    return out


def _targets(target) -> list:
    """The plain names one assignment target binds (``a``, ``a, b``, ``(a, *b)``)."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for elt in target.elts for n in _targets(elt)]
    if isinstance(target, ast.Starred):
        return _targets(target.value)
    return []


def _root(value):
    """The name an expression is read from: ``t`` for ``t``, ``t[1]``, ``t.copy`` ...; the store for ``_timers.find(x)[1]``."""
    while isinstance(value, (ast.Subscript, ast.Attribute, ast.Call)):
        value = value.func if isinstance(value, ast.Call) else value.value
    return value.id if isinstance(value, ast.Name) else None


def _timer_names(fn, store_names=frozenset(), fields=TIMER_FIELDS, handouts=frozenset()) -> set:
    """The names in one function that hold a timer: anything read or written by a timer field (``t["label"]``, ``t.get("label")``),
    anything the store hands out (``owner, t = _timers.find_by_id(i)``), and every ALIAS of those, followed to a fixed point
    (``alias = t``, ``a: dict = t``, ``(a := t)``, ``for a in (t,)``): ``alias.update(changes)`` is a timer change wherever the
    dict came from. (Review of #2688: an alias that no timer field was read through escaped.) Outside the store module only
    ``fields`` = the timer-only ones mark a name (a ``label`` or ``created_at`` is read off every kind of dict there), and so
    does a name bound from a function that hands out a stored timer (``handouts``)."""
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and _is_field(node.slice, fields):
            names.add(node.value.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                and node.func.attr == "get" and node.args and _is_field(node.args[0], fields):
            names.add(node.func.value.id)
    binds = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            binds += [(t, node.value) for t in node.targets]
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            binds.append((node.target, node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            binds.append((node.target, node.iter))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            binds += [(i.optional_vars, i.context_expr) for i in node.items if i.optional_vars is not None]
    for target, value in binds:
        parts = value.elts if isinstance(value, (ast.Tuple, ast.List, ast.Set)) else [value]
        if handouts and any(_hands_out(v, handouts) for v in parts):
            names |= set(_targets(target))
    sources = set(names) | set(store_names)
    while True:
        found = set()
        for target, value in binds:
            parts = value.elts if isinstance(value, (ast.Tuple, ast.List, ast.Set)) else [value]
            if {_root(v) for v in parts} & sources:
                found |= set(_targets(target))
        found -= sources
        if not found:
            return names
        names |= found
        sources |= found


def _dict_write(node, timers: set, fields=TIMER_FIELDS, handouts=frozenset()):
    """A timer changed through a dict METHOD rather than a subscript: ``t.update({"announced": True})`` / ``t.update(announced=
    True)`` / ``t.pop("announced")`` / ``t.setdefault("announced", ...)`` on anything, and ANY ``update`` / ``pop`` /
    ``setdefault`` / ``clear`` / ``popitem`` / ``|=`` on a name the function uses as a timer. Returns what to report, or None."""
    if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr) and isinstance(node.target, ast.Name) and node.target.id in timers:
        return f"{node.target.id} |="
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in DICT_WRITES):
        return None
    method, args = node.func.attr, node.args
    names_a_field = (
        (method in ("pop", "setdefault", "__setitem__", "__delitem__") and args and _is_field(args[0], fields))
        or (method == "update" and (any(k.arg in fields for k in node.keywords)
                                    or any(isinstance(a, ast.Dict) and any(_is_field(k, fields) for k in a.keys) for a in args))))
    on_a_timer = (isinstance(node.func.value, ast.Name) and node.func.value.id in timers) or _hands_out(node.func.value, handouts)
    return f".{method}()" if names_a_field or on_a_timer else None


def reaches_the_store(tree) -> bool:
    """The module imports the store module (any form: ``import``, ``from ... import``, ``importlib.import_module``,
    ``__import__``): it can be handed a stored timer, so it is scanned for timer writes as the store module is."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == STORE_MODULE for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == STORE_MODULE:
            return True
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("import_module", "__import__") \
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == STORE_MODULE:
            return True
    return False


def _foreign_callees(tree) -> set:
    """Names this module imports from ANOTHER of the service's modules (the standard library aside): a timer handed to one of
    them has left the store module's sight."""
    import sys

    def ours(name):
        return name.split(".")[0] not in sys.stdlib_module_names
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {(a.asname or a.name).split(".")[0] for a in node.names if ours(a.name)}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0 and ours(node.module) and node.module != STORE_MODULE:
            out |= {a.asname or a.name for a in node.names}
    return out


def timer_offences(source: str, *, is_store_module: bool, handouts=None) -> list:
    """Where this module changes a timer, or holds the store, outside ``_TimerStore``: ``(what, where)`` pairs.

    EVERY module is scanned for timer writes (review of #2688, round 3: other modules were not, and a field written outside the
    store module was asserted fine). How closely depends on whether it can be handed a stored timer:
    - the store module and every module that imports it: a write of ANY timer field (subscript or dict method) by any name, a
      dict write on any name holding a timer (one the store hands out: ``handed_out``, or read by a timer field), and a timer
      handed to another of the service's modules;
    - every other module: a write of a timer-only field (``TIMER_ONLY_FIELDS``) by any name, and a dict write on a name holding
      one, wherever the dict came from."""
    tree = ast.parse(source)
    if handouts is None:
        handouts = HANDED_OUT
    reaches = is_store_module or reaches_the_store(tree)
    fields = TIMER_FIELDS if reaches else TIMER_ONLY_FIELDS
    seeded_by = TIMER_FIELDS if is_store_module else TIMER_ONLY_FIELDS       # (elsewhere a ``label`` is read off any dict)
    store_names = _store_names(tree)
    functions = [fn for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))]
    timers_in = {fn.name: _timer_names(fn, store_names, seeded_by, handouts if reaches else frozenset()) for fn in functions}
    top = ast.Module(body=[n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))],
                     type_ignores=[])
    timers_in["<module>"] = _timer_names(top, store_names, seeded_by, handouts if reaches else frozenset())
    inside = {id(n) for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef) and cls.name == STORE_CLASS for n in ast.walk(cls)}
    owner = {}
    for fn in functions:
        for n in ast.walk(fn):
            owner.setdefault(id(n), fn.name)
    foreign = _foreign_callees(tree) if reaches else set()
    out = []
    for node in ast.walk(tree):
        if id(node) in inside:
            continue
        where = owner.get(id(node), "<module>")
        timers = timers_in.get(where, set())
        if isinstance(node, ast.Attribute) and node.attr in STORE_PRIVATE:
            out.append((node.attr, where))                                   # the registry itself, or its snapshot, from outside
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) and _is_field(node.slice, fields):
            out.append((f"[{node.slice.value!r}] =", where))                 # a timer's field written outside the store
        elif what := _dict_write(node, timers, fields, handouts if reaches else frozenset()):
            out.append((what, where))                                        # ... or written through a dict method
        elif reaches and isinstance(node, ast.Call) and _root(node.func) in foreign \
                and any(isinstance(a, ast.Name) and a.id in timers for a in [*node.args, *(k.value for k in node.keywords)]):
            out.append((f"hands a timer to {_root(node.func)}", where))     # the dict itself leaves for another module
        if not is_store_module:
            if isinstance(node, ast.Name) and node.id in store_names and isinstance(node.ctx, ast.Load):
                out.append((node.id, where))                                 # another module holding the store
            elif isinstance(node, ast.Attribute) and node.attr in (STORE, STORE_CLASS):
                out.append((node.attr, where))
            elif isinstance(node, ast.ImportFrom) and node.module == STORE_MODULE and any(a.name in (STORE, STORE_CLASS) for a in node.names):
                out.append(("import", where))
    return sorted(out)          # every occurrence (the allowlist is by exact count)


HANDED_OUT = handed_out((ROOT / "skybridge_service.py").read_text("utf-8"))


def test_only_the_timer_store_changes_a_timer():
    """Every module, each scanned as ``timer_offences`` says; the only timer-field writes outside the store are the ones
    ``ALLOWED`` names, each by module + function + exact count.
    Controls run on the real tree (review of #2688, round 3; each failed this test naming the plant, then restored):
    ``gone = cancel_any_timer(i)`` + ``gone["expires_at"] = 0`` planted in ``routers/skybridge.py``; the same with
    ``gone.update(changes)``; ``t["announced"] = True`` planted in ``voice_announce.py`` (a module that never imports the store);
    ``voice_announce.keep(timer)`` planted in ``skybridge_service._queue_timer_done`` (a timer handed to another module); a
    second ``_READINESS_CACHE["expires_at"] = 0`` in ``main._build_readiness_report`` (the allowlist is by exact count)."""
    import collections

    modules = _modules()
    assert ROOT / "skybridge_service.py" in modules and ROOT / "routers" / "skybridge.py" in modules
    reaching = [p for p in modules if reaches_the_store(ast.parse(p.read_text("utf-8")))]
    assert ROOT / "intent_router.py" in reaching and ROOT / "routers" / "skybridge.py" in reaching and len(reaching) >= 8
    seen, offenders = collections.Counter(), {}
    for p in modules:
        rel = p.relative_to(ROOT).as_posix()
        for what, where in timer_offences(p.read_text("utf-8"), is_store_module=rel == "skybridge_service.py"):
            if (rel, where, what) in ALLOWED:
                seen[(rel, where, what)] += 1
            else:
                offenders.setdefault(rel, []).append((what, where))
    assert not offenders, offenders
    assert dict(seen) == {k: n for k, (n, _why) in ALLOWED.items()}           # each allowed write, exactly as many times
    # the functions that hand out a stored timer are found (the derivation is not vacuous), and the store module hands no
    # timer to another module today
    assert HANDED_OUT == {"cancel_any_timer", "cancel_timer_by_id"}
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
                       "def fine():\n    return _timers.create()\n"
                       "def reload():\n    _timers._load()\n    _timers._rings.clear()\n")
    assert timer_offences(in_store_module, is_store_module=True) == [
        ("['announced'] =", "ring"), ("_by_owner", "reach"), ("_load", "reload"), ("_rings", "reload"), ("_save", "snapshot")]
    # a timer changed through a dict method (review of #2688: only subscript writes were read, so each of these escaped)
    for body, what in (('t.update({"announced": True})', ".update()"), ("t.update(announced=True)", ".update()"),
                       ('t.pop("voice_failed", None)', ".pop()"), ('t.setdefault("announced", True)', ".setdefault()"),
                       ('if t["label"]:\n        t.clear()', ".clear()"), ('label = t.get("label")\n    t.update(changes)', ".update()"),
                       ('t["label"]\n    t |= {"x": 1}', "t |="), ('t.__setitem__("announced", True)', ".__setitem__()")):
        planted = f"class _TimerStore:\n    def spoke(self, t):\n        t.update({{'announced': True}})\nasync def _ring_when_done(t, changes):\n    {body}\n"
        assert timer_offences(planted, is_store_module=True) == [(what, "_ring_when_done")], body
    # ... and through an ALIAS (review of #2688, round 2: a name no timer field was read through escaped). Controls run: with
    # ``_timer_names`` not following bindings (the fixed point removed) these escape; a planted ``owner, t =
    # _timers.find_by_id(i); alias = t; alias.update(changes)`` in ``skybridge_service.py`` fails the scan of the real code.
    for body in ('t = _timers.find_by_id(i)[1]\n    alias = t\n    alias.update(changes)',
                 'owner, t = _timers.find_by_id(i)\n    alias = t\n    again = alias\n    again.update(changes)',
                 'label = t["label"]\n    alias: dict = t\n    alias.update(changes)',
                 'label = t["label"]\n    if (alias := t):\n        pass\n    other = alias\n    other.pop("announced")',
                 'label = t["label"]\n    for alias in (t,):\n        alias.clear()'):
        planted = ("class _TimerStore:\n    def spoke(self, t):\n        t.update({'announced': True})\n_timers = _TimerStore()\n"
                   f"def edit(i, t, changes):\n    {body}\n")
        assert [w for _, w in timer_offences(planted, is_store_module=True)] == ["edit"], body
    # a dict that is not a timer, and a read, are not offences
    assert not timer_offences("def f(d, t):\n    d.update({'x': 1})\n    d.pop('y')\n    d.clear()\n    return t.get('label'), t['label']\n",
                              is_store_module=True)
    elsewhere = ("import skybridge_service as sky\nfrom skybridge_service import _timers as store, cancel_any_timer\n"
                 "mine = store\nagain = mine\n"
                 "def a():\n    return again.create('u', 'x', 1)\n"
                 "def b():\n    return sky._timers.cancel_any('t')\n"
                 "def fine():\n    return cancel_any_timer('t')\n")
    found = timer_offences(elsewhere, is_store_module=False)
    assert ("again", "a") in found and ("_timers", "b") in found and ("import", "<module>") in found


def test_a_timer_changed_in_any_other_module_is_caught():
    """Review of #2688, round 3: the field and dict-method checks ran only on the store module, and a field written elsewhere
    was asserted fine. Every module is scanned now. Controls run: with the scan back on the store module only (``fields`` =
    empty outside it) the planted writes below escape; with ``handed_out`` returning nothing the hand-out cases escape."""
    sky = "from skybridge_service import cancel_any_timer, active_timers_for\nimport skybridge_service as sky\nimport voice_cards\n"
    for body, what in (("t['label'] = 'x'", "['label'] ="),                              # the case this test once asserted fine
                       ("gone = cancel_any_timer(i)\n    gone['expires_at'] = 0", "['expires_at'] ="),
                       ("del t['panel_id']", "['panel_id'] ="),
                       ("t.update({'expires_at': 0})", ".update()"),
                       ("gone = cancel_any_timer(i)\n    gone.update(changes)", ".update()"),
                       ("gone = sky.cancel_timer_by_id(o, i)\n    again = gone\n    again.clear()", ".clear()"),
                       ("sky.cancel_timer_by_id(o, i).update(changes)", ".update()"),
                       ("gone = cancel_any_timer(i)\n    voice_cards.keep(gone)", "hands a timer to voice_cards")):
        planted = sky + f"def edit(t, i, o, changes):\n    {body}\n"
        assert timer_offences(planted, is_store_module=False) == [(what, "edit")], body
    # a module that never imports the store: a timer-only field, by any name, or a dict write on a name that holds one
    for body, what in (("t['announced'] = True", "['announced'] ="), ("t.update(voice_failed=True)", ".update()"),
                       ("t.pop('set_seconds')", ".pop()"), ("if t.get('announced'):\n        t.clear()", ".clear()")):
        assert timer_offences(f"def ring(t):\n    {body}\n", is_store_module=False) == [(what, "ring")], body
    # the store module handing a timer to another of the service's modules
    store = ("import json\nimport voice_announce\nclass _TimerStore:\n    pass\n"
             "def ring(t):\n    voice_announce.keep(t, t['label'])\n    json.dumps(t)\n")
    assert timer_offences(store, is_store_module=True) == [("hands a timer to voice_announce", "ring")]
    # NOT offences: a card is a copy (``active_timers_for`` returns props), a dict that is not a timer, a field READ elsewhere
    assert not timer_offences(sky + "def show(o, x):\n    cards = active_timers_for(o)\n    cards[0].update(x)\n    return cancel_any_timer(o)['label']\n",
                              is_store_module=False)
    assert not timer_offences("def f(d, t):\n    d['label'] = 'x'\n    d.update(created_at=1)\n    return t['announced']\n", is_store_module=False)
    # the hand-out functions are read from the code: a return of the store's own dicts, as they are, counts; a card does not
    found = handed_out("_timers = None\ndef a(o):\n    return _timers.list(o)\ndef b(o):\n    return [t for t in _timers.list(o)]\n"
                       "def c(o):\n    return [_card(t) for t in _timers.list(o)]\ndef d(o):\n    return len(_timers.list(o))\n")
    assert found == {"a", "b"}
