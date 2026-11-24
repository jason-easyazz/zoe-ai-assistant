"""Every ENTRY POINT of the lists door (each function outside ``list_service`` that names a writing function of it), driven
through its own code against one recording store: every statement with its values, every read with its arguments, what it
announces, what it returns, and the list and item rows as its writes leave them.

``tests/lists_door_scenarios.py`` pins the door functions as called directly; this pins what each CALLER hands them, so an
entry point that changes the arguments it supplies (a name, a visibility, a list it picks) changes the record.

Used twice: ``python3 tests/lists_entry_scenarios.py <out.json>`` with the CWD a zoe-data tree prints the record of THAT tree
(run against an export of ``main`` to make ``tests/fixtures/lists_entry_points_as_on_main.json``), and
``test_lists_entry_points_same_as_main.py`` runs the same scenarios with the lists flag off and compares. It imports nothing the
door work added, so it runs unchanged on ``main``.

The store answers a read from its rows, filtered by the plain ``column = value`` terms of the statement's WHERE (a term it
cannot read does not filter), and APPLIES each write whose every term it can read; a write it cannot read is named in
``unapplied``. Invented ids and words only. The clock is frozen; each id made at random is replaced by ``<id-n>``, ``n`` counted
in order of first appearance across the scenario's whole record (its log, then ``unapplied``, then the rows), so two new rows
stay told apart and a row naming another new row (an item's ``list_id``) keeps naming THAT row.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sys
from datetime import datetime, timezone

OWNER, OTHER = "member-ova", "member-adu"
WORD, NAME = "womble", "Vellum"
FROZEN = datetime(2026, 6, 10, 4, 0, 0, 123456, tzinfo=timezone.utc)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MARK = re.compile(r"\x00(\d+)\x00")


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN.astimezone(tz) if tz else FROZEN.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return FROZEN.replace(tzinfo=None)


def _mask(value):
    """JSON-ready: datetimes as ISO text. Random ids are left as they are here and numbered once over the whole record
    (``_number``), so the same id gets the same placeholder in a statement, a reply and a row."""
    if isinstance(value, dict):
        return {_mask(k): _mask(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _number(value, ids: dict):
    """``value`` with each UUID replaced by ``<id-n>``: the same id always the same ``n``, a new id the next ``n`` (``ids`` holds
    the numbering so far, in order of first appearance)."""
    if isinstance(value, str):
        return _UUID.sub(lambda m: ids.setdefault(m[0], f"<id-{len(ids) + 1}>"), value)
    if isinstance(value, dict):
        return {_number(k, ids): _number(v, ids) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_number(v, ids) for v in value]
    return value


class Row(dict):
    """A row read by column name or by position (as both drivers' rows are)."""

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else dict.__getitem__(self, key)


class _Cur:
    def __init__(self, rows=(), rowcount=0):
        self.rows, self.rowcount, self.lastrowid = [Row(r) for r in rows], rowcount, None

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return list(self.rows)

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def _marked(sql: str, params: list) -> tuple[str, dict]:
    """The statement with each placeholder (``?`` in order, ``$n`` by number) replaced by a marker naming its value."""
    values, n = {}, [0]

    def q(_m):
        n[0] += 1
        values[n[0]] = params[n[0] - 1] if n[0] - 1 < len(params) else None
        return f"\x00{n[0]}\x00"

    def d(m):
        k = int(m[1])
        values[1000 + k] = params[k - 1] if k - 1 < len(params) else None
        return f"\x00{1000 + k}\x00"

    sql = re.sub(r"\$(\d+)", d, sql)
    sql = re.sub(r"\?", q, sql)
    return sql, values


_NOPE = object()


def _value(token: str, values: dict):
    token = token.strip()
    if "||" in token:                                       # a concatenation of readable parts
        parts = [_value(re.sub(r"^lower\((.*)\)$", r"\1", p.strip(), flags=re.I), values) for p in token.split("||")]
        return _NOPE if any(p is _NOPE or p is None for p in parts) else "".join(str(p) for p in parts)
    m = _MARK.fullmatch(token)
    if m:
        return values[int(m[1])]
    up = token.upper()
    if up in ("NOW()", "CURRENT_TIMESTAMP"):
        return "<now>"
    if up == "NULL":
        return None
    if up in ("TRUE", "FALSE"):
        return up == "TRUE"
    if re.fullmatch(r"'[^']*'", token):
        return token[1:-1]
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    return _NOPE


def _split_top(text: str, sep: str) -> list[str]:
    """Split on ``sep`` outside parentheses and quotes."""
    out, depth, cur, i, quote = [], 0, "", 0, False
    while i < len(text):
        c = text[i]
        if c == "'":
            quote = not quote
        if not quote:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif depth == 0 and text[i:i + len(sep)].upper() == sep.upper():
                out.append(cur)
                cur, i = "", i + len(sep)
                continue
        cur += c
        i += 1
    out.append(cur)
    return [p.strip() for p in out]


_TERM = re.compile(r"^(lower\()?(?:\w+\.)?(\w+)\)?\s*(=|<>|!=)\s*(.+?)$", re.I)
_NULL = re.compile(r"^(?:\w+\.)?(\w+)\s+IS\s+(NOT\s+)?NULL$", re.I)


def _one_term(term: str, values: dict):
    """``(column, op, value, lowered)`` for one plain term, ``("OR", [terms])`` for a parenthesised OR of plain terms; else None."""
    term = term.strip()
    while term.startswith("(") and term.endswith(")") and _split_top(term[1:-1], " AND ") == [term[1:-1].strip()]:
        inner = term[1:-1].strip()
        if len(_split_top(inner, " OR ")) > 1:
            alts = [_one_term(t, values) for t in _split_top(inner, " OR ")]
            return ("OR", alts) if None not in alts else None
        term = inner
    m = _NULL.match(term)
    if m:
        return (m[1], "<>" if m[2] else "=", None, False)
    m = _TERM.match(term)
    if not m or len(_split_top(term, " OR ")) > 1:
        return None
    lowered, rhs = bool(m[1]), m[4].strip()
    if lowered:
        rhs = re.sub(r"^lower\((.*)\)$", r"\1", rhs, flags=re.I) if re.match(r"^lower\(", rhs, re.I) else rhs
    value = _value(rhs, values)
    if value is _NOPE:
        return None
    return (m[2], "=" if m[3] == "=" else "<>", value, lowered)


def _terms(where: str, values: dict):
    """The read terms of a WHERE; ``None`` in the list for a term not read."""
    where = re.split(r"\b(?:ORDER BY|LIMIT|GROUP BY|RETURNING|FOR UPDATE|FOR SHARE)\b", where, flags=re.I)[0]
    return [_one_term(t, values) for t in _split_top(where, " AND ")] if where.strip() else []


def _test(row: dict, t):
    """True / False for a term this row answers; None when the term or its column is not read."""
    if t is None:
        return None
    if t[0] == "OR":
        res = [_test(row, alt) for alt in t[1]]
        return True if True in res else (False if all(r is False for r in res) else None)
    col, op, want, lowered = t
    if col not in row:
        return None
    have = row[col]
    if lowered and isinstance(have, str) and isinstance(want, str):
        have, want = have.lower(), want.lower()
    if isinstance(want, bool) or isinstance(have, bool):
        have, want = bool(have), bool(want)
    elif (isinstance(want, int) or isinstance(have, int)) and not (isinstance(want, int) and isinstance(have, int)):
        have, want = str(have), str(want)
    return (have == want) == (op == "=")


def _matches(row: dict, terms) -> bool:
    """A read: every term the row answers holds (a term not read does not filter)."""
    return all(_test(row, t) is not False for t in terms)


class Store:
    """The household's lists: ``L1`` (the shopping list, family), ``L2`` (the owner's private personal list) and ``L3`` (a
    family list the owner made), the first two with an item each."""

    def __init__(self, log):
        self.log, self.unapplied, self.depth, self.committed = log, [], 0, 0
        base = {"description": "", "created_at": "2026-06-01T00:00:00", "updated_at": "2026-06-01T00:00:00", "deleted": 0}
        self.tables = {
            "lists": {
                "L1": {"id": "L1", "user_id": OWNER, "name": "Shopping", "list_type": "shopping", "visibility": "family", **base},
                "L2": {"id": "L2", "user_id": OWNER, "name": "Personal", "list_type": "personal", "visibility": "personal", **base},
                "L3": {"id": "L3", "user_id": OWNER, "name": "Garden", "list_type": "custom", "visibility": "family", **base},
            },
            "list_items": {
                "I1": {"id": "I1", "list_id": "L1", "text": "wombel", "completed": 0, "priority": "medium", "category": None,
                       "quantity": None, "parent_id": None, "sort_order": 0, **base},
                "I2": {"id": "I2", "list_id": "L2", "text": "quince", "completed": 0, "priority": "medium", "category": None,
                       "quantity": None, "parent_id": None, "sort_order": 0, **base},
            },
            "users": {OWNER: {"id": OWNER, "role": "user", "name": "Member"}, OTHER: {"id": OTHER, "role": "user", "name": "Member"}},
        }

    # -- the driver surface (aiosqlite-compatible execute, asyncpg fetch / fetchrow / fetchval / transaction) --------------------
    def execute(self, sql, *params):
        return _Cur(*self._run(sql, params))

    async def fetch(self, sql, *params):
        return self._run(sql, params, kind="fetch")[0]

    async def fetchrow(self, sql, *params):
        rows = self._run(sql, params, kind="fetchrow")[0]
        return rows[0] if rows else None

    async def fetchval(self, sql, *params):
        rows = self._run(sql, params, kind="fetchval")[0]
        return list(rows[0].values())[0] if rows else None

    async def commit(self):
        self.committed += 1
        self.log.append(["commit"])

    async def rollback(self):
        self.log.append(["rollback"])

    async def close(self):
        pass

    def transaction(self):
        store = self

        class _T:
            async def __aenter__(self):
                store.depth += 1

            async def __aexit__(self, exc_type, *a):
                store.depth -= 1
                store.log.append(["transaction", "committed" if exc_type is None else "rolled back"])
                return False
        return _T()

    # -- the record ----------------------------------------------------------------------------------------------------------
    def _run(self, sql, params, kind="execute"):
        norm = " ".join(str(sql).split())
        values = list(params[0]) if len(params) == 1 and isinstance(params[0], (list, tuple)) else list(params)
        self.log.append(["sql", kind, _mask(norm), _mask(values)])
        head = norm.split()[0].upper() if norm else ""
        try:
            if head in ("INSERT", "UPDATE", "DELETE"):
                return [], self._write(head, norm, values)
            if head in ("SELECT", "WITH"):
                return self._select(norm, values), 0
        except (ValueError, KeyError, IndexError, TypeError):
            self.unapplied.append(_mask(norm))
        return [], 0

    def _write(self, head, norm, values) -> int:
        sql, vals = _marked(norm, values)
        if head == "INSERT":
            m = re.match(r"INSERT INTO (\w+) \(([^)]*)\) VALUES \((.*?)\)( ON CONFLICT \((.*?)\)(?: WHERE .*?)? DO NOTHING)?$", sql, re.I)
            if not m or m[1] not in self.tables:
                raise ValueError(norm)
            cols = [c.strip() for c in m[2].split(",")]
            row = {c: _value(v, vals) for c, v in zip(cols, _split_top(m[3], ","))}
            if _NOPE in row.values():
                raise ValueError(norm)
            if m[4]:                                        # ON CONFLICT (cols) DO NOTHING: a live row with those values stops it
                keys = [re.sub(r"^lower\((\w+)\)$", r"\1", k.strip(), flags=re.I) for k in _split_top(m[5], ",")]
                fold = lambda v: v.lower() if isinstance(v, str) else v  # noqa: E731
                if any(not r.get("deleted") and all(fold(r.get(k)) == fold(row.get(k)) for k in keys)
                       for r in self.tables[m[1]].values()):
                    return 0
            self.tables[m[1]][row.get("id", len(self.tables[m[1]]))] = {"deleted": 0, **row}
            return 1
        m = (re.match(r"UPDATE (\w+) SET (.*?) WHERE (.*)$", sql, re.I) if head == "UPDATE"
             else re.match(r"DELETE FROM (\w+) WHERE (.*)$", sql, re.I))
        if not m or m[1] not in self.tables:
            raise ValueError(norm)
        terms = _terms(m[3] if head == "UPDATE" else m[2], vals)
        if None in terms or re.search(r"\b(RETURNING|IN \(SELECT|EXISTS)\b", sql, re.I):
            raise ValueError(norm)
        new = {}
        if head == "UPDATE":
            for part in _split_top(m[2], ","):
                col, _, tok = part.partition("=")
                value = _value(tok, vals)
                if value is _NOPE:
                    raise ValueError(norm)
                new[col.strip()] = value
        table = self.tables[m[1]]
        answers = {k: [_test(row, t) for t in terms] for k, row in table.items()}
        if any(None in a for a in answers.values()):
            raise ValueError(norm)                          # a term this store cannot answer: never guess which row it meant
        hit = [k for k, a in answers.items() if all(a)]
        for k in hit:
            if head == "UPDATE":
                table[k].update(new)
            else:
                del table[k]
        return len(hit)

    def _select(self, norm, values):
        sql, vals = _marked(norm, values)
        if not re.match(r"SELECT ", sql, re.I):
            return []
        head = _split_top(sql[len("SELECT "):], " FROM ")
        if len(head) < 2:
            return [Row({"ok": 1})] if "pg_advisory" in sql else []
        cols, rest = head[0], " FROM ".join(head[1:])
        parts = _split_top(rest, " WHERE ")
        source, where = parts[0], " WHERE ".join(parts[1:])
        names = re.findall(r"\b(lists|list_items|users)\b", source)
        if not names:
            return []
        rows = self._joined(names)
        terms = _terms(where, vals)
        rows = [r for r in rows if _matches(r, terms)]
        if re.match(r"COUNT\(", cols.strip(), re.I):
            alias = re.search(r"COUNT\([^)]*\)\s+AS\s+(\w+)", cols, re.I)
            return [Row({(alias[1] if alias else "count"): len(rows)})]
        limit = re.search(r"\bLIMIT (\d+)", where or source, re.I)
        rows = rows[: int(limit[1])] if limit else rows
        return [self._project(r, cols) for r in rows]

    def _joined(self, names):
        live = lambda t: [dict(r) for r in self.tables[t].values() if not r.get("deleted")]  # noqa: E731
        if "list_items" in names and "lists" in names:
            by_id = self.tables["lists"]
            out = []
            for item in live("list_items"):
                owner = by_id.get(item["list_id"])
                if owner and not owner.get("deleted"):
                    out.append({**{k: v for k, v in owner.items() if k != "id"}, "name": owner["name"], **item,
                                "visibility": owner["visibility"], "list_type": owner["list_type"], "user_id": owner["user_id"]})
            return out
        return live(names[0])

    def _project(self, row: dict, cols: str) -> Row:
        if cols.strip() in ("*", "1"):
            return Row(row) if cols.strip() == "*" else Row({"?column?": 1})
        out = {}
        for col in _split_top(cols, ","):
            alias = re.search(r"\s+AS\s+(\w+)$", col, re.I)
            name = alias[1] if alias else re.sub(r"^.*\.", "", col.strip())
            source = re.sub(r"\s+AS\s+\w+$", "", col, flags=re.I).strip()
            bare = re.sub(r"^\w+\.", "", source)
            if re.match(r"\(SELECT COUNT\(\*\) FROM list_items", source, re.I):      # a list's live-item count
                out[name] = sum(1 for i in self.tables["list_items"].values() if i["list_id"] == row.get("id") and not i.get("deleted"))
            else:
                out[name] = row.get(bare) if re.fullmatch(r"\w+", bare) else row.get(name)
        return Row(out)

    def record_rows(self, ids: dict | None = None) -> dict:
        """Every list and item row, ids numbered with ``ids`` (in table order, so a new id first seen here is numbered the same
        on every run) and only then sorted."""
        ids = {} if ids is None else ids
        return {t: sorted((_number(_mask(dict(r)), ids) for r in self.tables[t].values()),
                          key=lambda r: json.dumps(r, sort_keys=True, default=str))
                for t in ("lists", "list_items")}


_UNDO: list = []
DEBUG = False


def _set(obj, name, value) -> None:
    _UNDO.append((obj, name, getattr(obj, name)))
    setattr(obj, name, value)


def _restore() -> None:
    while _UNDO:
        obj, name, value = _UNDO.pop()
        setattr(obj, name, value)


def _wire(log, store):
    """Every handle an entry point opens is this store; every announcement and clock is recorded or frozen."""
    import database
    import db_compat
    import intent_router
    import list_service
    import mcp_server
    from push import broadcaster

    @contextlib.asynccontextmanager
    async def handle(*_a, **_k):
        yield store

    _set(database, "get_db_ctx", handle)
    _set(db_compat, "get_compat_db", handle)
    _set(mcp_server, "_pg_get_db", handle)
    try:
        import db_pool

        _set(db_pool, "get_db_ctx", handle)
    except ImportError:
        pass

    async def broadcast(channel, event, data, **k):
        log.append(["broadcast", channel, event, _mask(json.loads(json.dumps(data, default=str))), sorted(k)])

    async def notify(*a, **k):
        log.append(["notify_ui", _mask(json.loads(json.dumps([a, k], default=str)))])

    _set(broadcaster, "broadcast", broadcast)
    _set(mcp_server, "_notify_ui", notify)
    _set(intent_router, "_notify_lists_ui", notify)

    async def actor(db, name, args, ctx):
        return {"user_id": (ctx or {}).get("user_id", OWNER), "role": "user"}

    _set(mcp_server, "_resolve_mcp_actor", actor)

    async def user(_db, uid):
        return {"user_id": uid, "role": "user", "username": "Member"}

    _set(intent_router, "_load_direct_execution_user", user)
    import card_service

    real_build = card_service.CardService.build_card

    def build_card(self, *a, **k):          # the card's own stamp is read from the frozen clock (its clock is a local import)
        return {**real_build(self, *a, **k), "created_at": FROZEN.isoformat().replace("+00:00", "Z")}

    _set(card_service.CardService, "build_card", build_card)
    import anticipation
    import list_sharing
    import omi_review_apply
    import pending_suggestions
    import skybridge_service
    from routers import lists as rest

    for module in (list_service, rest, skybridge_service, intent_router, mcp_server, pending_suggestions, anticipation,
                   list_sharing, omi_review_apply):
        if getattr(module, "datetime", None) is datetime:
            _set(module, "datetime", _Clock)
    for name in ("_RECENT_ADDS",):
        cache = getattr(list_service, name, None)
        if isinstance(cache, dict):
            saved = dict(cache)
            cache.clear()
            _UNDO.append((cache, "__restore__", saved))
    return rest


def _user(who=OWNER):
    return {"user_id": who, "role": "user", "username": "Member"}


def scenarios():
    """name -> a coroutine function of (rest, store)."""
    from models import ListCreate, ListItemCreate, ListItemUpdate, ListUpdate

    def sky(action, **fields):
        async def run(rest, store):
            import skybridge_service

            intent = skybridge_service.SkybridgeIntent(domain="lists", action=action, **fields)
            return await skybridge_service._resolve_with_db(intent, OWNER, store, context=None)
        return run

    def spoken(name, **slots):
        async def run(rest, store):
            import intent_router

            return await intent_router.execute_intent(intent_router.Intent(name, slots), OWNER)
        return run

    def tool(name, **args):
        async def run(rest, store):
            import mcp_server

            return await mcp_server._execute_tool(store, name, args, {"user_id": OWNER})
        return run

    async def anticipation_packing(rest, store):
        import anticipation

        ev = type("Ev", (), {"title": WORD})()
        offer = {"kind": "packing", "slots": {"start_date": "2099-06-16", "event_kind": "flight"}}
        return list(await anticipation._do(store, OWNER, offer, ev, FROZEN))

    async def suggestion(rest, store, slots):
        import pending_suggestions

        return await pending_suggestions._execute_action(store, "list_add", slots, OWNER, [])

    async def pendant(rest, store):
        import omi_review_apply

        return await omi_review_apply.write_list_item(OWNER, {"id": "cand_bar_0001", "payload": {"list": "shopping", "item": WORD}}, "panel")

    async def share(rest, store, list_id, on):
        import list_sharing

        return await list_sharing.set_list_visibility(store, list_id=list_id, user_id=OWNER, share=on)

    return {
        # the screen's REST routes
        "rest_create_list": lambda rest, store: rest.create_list("personal", ListCreate(name=NAME, visibility="personal"),
                                                                 user=_user(), db=store),
        "rest_rename_list": lambda rest, store: rest.update_list("personal", "L2", ListUpdate(name=NAME), user=_user(), db=store),
        "rest_delete_list": lambda rest, store: rest.delete_list("personal", "L2", user=_user(), db=store),
        "rest_add_item": lambda rest, store: rest.add_item("shopping", "L1", ListItemCreate(text=WORD), user=_user(), db=store,
                                                           request=None),
        "rest_edit_item": lambda rest, store: rest.update_item("shopping", "L1", "I1", ListItemUpdate(text=WORD, completed=True),
                                                               user=_user(), db=store, request=None),
        "rest_delete_item": lambda rest, store: rest.delete_item("shopping", "L1", "I1", user=_user(), db=store, request=None),
        # the brain's tools
        "tool_add_item": tool("list_add_item", list_type="shopping", text=f"{WORD} and quince"),
        "tool_remove_item": tool("list_remove_item", list_type="shopping", item_text="wombel"),
        # the spoken paths
        "spoken_add": spoken("list_add", item=WORD, list_type="shopping"),
        "spoken_remove": spoken("list_remove", item="wombel", list_type="shopping"),
        "spoken_rename": spoken("list_rename", item="wombel", new_item=WORD, list_type="shopping"),
        # skybridge (typed and panel commands)
        "skybridge_add": sky("add_item", list_type="shopping", item_text=f"{WORD} and kumquat"),
        "skybridge_add_to_a_new_list": sky("add_item", list_type="tasks", item_text=WORD),
        "skybridge_remove": sky("remove_item", list_type="shopping", item_text="wombel"),
        "skybridge_tick": sky("complete_item", list_type="shopping", item_text="wombel", completed=True),
        "skybridge_rename": sky("rename_item", list_type="shopping", item_text="wombel", target_text=WORD),
        "skybridge_create_list": sky("create_list", list_type="personal", list_name=NAME),
        "skybridge_share": sky("share_list", list_type="personal", list_name="Personal"),
        # the background makers
        "anticipation_packing": anticipation_packing,
        "suggestion_list_add": lambda rest, store: suggestion(rest, store, {"item": WORD, "list_type": "shopping"}),
        "suggestion_list_add_new_list": lambda rest, store: suggestion(rest, store, {"item": WORD, "list_type": "tasks"}),
        "pendant_list_item": pendant,
        "share_a_private_list": lambda rest, store: share(rest, store, "L2", True),
        "unshare_a_shared_list": lambda rest, store: share(rest, store, "L3", False),
        "unshare_the_household_shopping_list": lambda rest, store: share(rest, store, "L1", False),
    }


def _isolated() -> None:
    """Refuse to run outside the test configuration: a list change reaches stores that default under the live ``~/.zoe`` (the
    shadow's log, the journals). ``tests/conftest.py`` points each at a temporary directory."""
    import os

    live = os.path.join(os.path.expanduser("~"), ".zoe")
    for var in ("ZOE_LOG_DIR", "MEMPALACE_DATA_DIR", "ZOE_MEMORY_REJECT_LEDGER"):
        value = os.environ.get(var, "")
        if not value or os.path.abspath(value).startswith(live):
            raise RuntimeError(f"lists entry scenarios need the test configuration ({var} is not isolated): import tests/conftest.py first")
    if os.environ.get("ZOE_OS_LISTS", "").strip().lower() == "kernel":
        raise RuntimeError("lists entry scenarios record the flag-off door: ZOE_OS_LISTS must not be 'kernel'")


def run_one(name: str) -> dict:
    _isolated()
    call = scenarios()[name]
    log: list = []
    store = Store(log)
    rest = _wire(log, store)
    try:
        result = asyncio.run(call(rest, store))
        log.append(["returned", _mask(json.loads(json.dumps(result, default=str)))])
    except Exception as exc:  # noqa: BLE001 - a refusal is part of the record
        if DEBUG:
            import traceback

            traceback.print_exc()
        detail = _mask(str(exc.detail)) if hasattr(exc, "detail") else type(exc).__name__
        log.append(["raised", getattr(exc, "status_code", None), detail])
    finally:
        while _UNDO:
            obj, attr, value = _UNDO.pop()
            if attr == "__restore__":
                obj.clear()
                obj.update(value)
            else:
                setattr(obj, attr, value)
    ids: dict = {}
    log = _number(json.loads(json.dumps(log, default=str)), ids)
    unapplied = _number(store.unapplied, ids)
    return json.loads(json.dumps({"log": log, "rows": store.record_rows(ids), "unapplied": unapplied}, default=str))


def run_all() -> dict:
    return {name: run_one(name) for name in scenarios()}


if __name__ == "__main__":      # python3 <this file> <out.json>, with the CWD the zoe-data to record (an export of main)
    sys.path[:0] = [".", "tests"]
    import conftest  # noqa: F401 - the CWD's test configuration: every live store path pinned to a temporary directory

    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(run_all(), fh, indent=1, sort_keys=True)
        fh.write("\n")
