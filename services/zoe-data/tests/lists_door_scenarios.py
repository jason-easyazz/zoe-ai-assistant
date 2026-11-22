"""Every lists door function, called once against one recording fake: each statement WITH its values, each read with its
arguments, and the list and item rows as the writes leave them.

Used twice: ``python3 tests/lists_door_scenarios.py <out.json>`` with the CWD a zoe-data tree prints the record of THAT tree
(run against an export of the commit before the door asked the kernel to make
``tests/fixtures/lists_door_flag_off_statements.json``), and ``test_lists_kernel_door.py`` runs the same calls with the flag off
and compares. It imports ``list_service`` only (when a call is made, so the CWD's own copy is the one recorded) and runs unchanged on the older tree.

Invented ids and words only. Ids made at random are masked.
"""
from __future__ import annotations

import inspect
import json
import re

OWNER, KID = "member-ova", "member-kip"
WORD, NAME = "womble", "Vellum"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_INSERT = re.compile(r"INSERT INTO (lists|list_items) \(([^)]*)\) VALUES \(([^)]*)\)$")
_UPDATE = re.compile(r"UPDATE (lists|list_items) SET (.*?) WHERE (.*)$")
_TERM = re.compile(r"(\w+)\s*(=|<>)\s*(.+)$")


def _mask(value):
    if isinstance(value, str):
        return _UUID.sub("<id>", value)
    if isinstance(value, dict):
        return {_mask(k): _mask(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask(v) for v in value]
    return value


class _Cur:
    def __init__(self, rows=()):
        self.rows = list(rows)

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return list(self.rows)


class _Values:
    """The values of one statement, read as its placeholders name them: ``?`` in order, ``$n`` by number."""

    def __init__(self, params):
        self.params, self.next = params, 0

    def of(self, token):
        token = token.strip()
        if token == "?":
            self.next += 1
            return self.params[self.next - 1]
        if re.fullmatch(r"\$\d+", token):
            return self.params[int(token[1:]) - 1]
        if token.upper() == "NOW()":
            return "<now>"
        if re.fullmatch(r"'[^']*'", token):
            return token[1:-1]
        if re.fullmatch(r"-?\d+", token):
            return int(token)
        raise ValueError(token)


class DB:
    """Records every statement with its values and APPLIES each write to one list row (``L1``) and its item rows, so a changed
    value, a changed row or a write that reaches the wrong row shows in ``rows``. Reads answer as they always did (one list row
    for the door's own lookup); a write this fake cannot read is named in ``unapplied``. ``transaction`` only when asked for."""

    def __init__(self, owner=OWNER, visibility="family", items=()):
        self.sql, self.fetched, self.owner, self.visibility, self.items = [], [], owner, visibility, list(items)
        self.params, self.fetch_args, self.unapplied = [], [], []
        self.depth = self.committed = 0
        self.rows = {"lists": {"L1": {"id": "L1", "user_id": owner, "name": "Shopping", "visibility": visibility, "deleted": 0}},
                     "list_items": {i["id"]: {"list_id": "L1", "deleted": 0, **i} for i in self.items}}

    async def execute(self, sql, *params):
        norm = " ".join(sql.split())
        values = list(params[0]) if len(params) == 1 and isinstance(params[0], (list, tuple)) else list(params)
        self.sql.append(norm)
        self.params.append(values)
        if norm.split()[0].upper() in ("INSERT", "UPDATE", "DELETE"):
            try:
                self._apply(norm, values)
            except (ValueError, IndexError, TypeError):
                self.unapplied.append(norm)
        return _Cur([{"id": "L1"}] if sql.lstrip().upper().startswith("SELECT") and "FROM lists" in sql else [])

    def _apply(self, norm, values) -> None:
        take = _Values(values)
        made = _INSERT.match(norm)
        if made:
            row = {c.strip(): take.of(v) for c, v in zip(made[2].split(","), made[3].split(","))}
            self.rows[made[1]][row["id"]] = {"deleted": 0, **row}
            return
        changed = _UPDATE.match(norm)
        sets = [_TERM.match(part.strip()) for part in changed[2].split(",")]
        new = {m[1]: take.of(m[3]) for m in sets}
        tests = [(m[1], m[2], take.of(m[3])) for m in (_TERM.match(part.strip()) for part in changed[3].split(" AND "))]
        for row in self.rows[changed[1]].values():
            if all((row.get(col) == want) == (op == "=") for col, op, want in tests):
                row.update(new)

    async def fetch(self, sql, *args):
        self.fetched.append(" ".join(sql.split()))
        self.fetch_args.append(list(args))
        if "FROM list_items WHERE list_id" in sql:
            return self.items
        return [{"id": "L1", "user_id": self.owner, "visibility": self.visibility}]

    async def commit(self):
        pass

    @property
    def writes(self):
        return [s for s in self.sql if s.split()[0].upper() in ("INSERT", "UPDATE", "DELETE")]

    def record(self) -> dict:
        """What one call did, as the fixture keeps it."""
        rows = {t: sorted(_mask(list(r.values())), key=lambda x: json.dumps(x, sort_keys=True)) for t, r in self.rows.items()}
        return {**_mask({"sql": self.sql, "params": self.params, "fetched": self.fetched, "fetch_args": self.fetch_args,
                         "unapplied": self.unapplied}), "rows": rows}


class TxDB(DB):
    fail_commit = False

    def transaction(self):
        db = self

        class _T:
            async def __aenter__(self):
                db.depth += 1

            async def __aexit__(self, exc_type, *a):
                db.depth -= 1
                if exc_type is None and db.depth == 0:
                    if db.fail_commit:
                        raise RuntimeError("commit failed")
                    db.committed += 1
                return False
        return _T()


def _call(fn_name, db, **kwargs):
    """Call one door function. An argument the tree's own function does not take is left out (the tree before the door asked
    the kernel has no ``stamps_at``); on this branch every argument is taken, so nothing is dropped here."""
    import list_service

    fn = getattr(list_service, fn_name)
    takes = inspect.signature(fn).parameters
    if not any(p.kind is p.VAR_KEYWORD for p in takes.values()):
        kwargs = {k: v for k, v in kwargs.items() if k in takes}
    return fn(db, **kwargs)


def _stamps():
    import list_service

    now = getattr(list_service, "stamps_now", None)
    return now() if now else None


#: every door function: (the kernel verb it asks, how to call it as ``who``)
DOORS = {
    "create_list_record": ("list.create", lambda db, who: _call(
        "create_list_record", db, user_id=who, name=NAME, list_type="personal", visibility="personal", stamps_at=_stamps())),
    "create_item_record": ("item.add", lambda db, who: _call("create_item_record", db, list_id="L1", text=WORD, user_id=who)),
    "remove_item_record": ("item.remove", lambda db, who: _call("remove_item_record", db, user_id=who, item_id="I1", list_id="L1")),
    "set_item_checked": ("item.tick", lambda db, who: _call("set_item_checked", db, user_id=who, item_id="I1", completed=1)),
    "update_item_record": ("item.edit", lambda db, who: _call(
        "update_item_record", db, user_id=who, item_id="I1", list_id="L1", updates=["text = ?"], params=[WORD, "I1", "L1"])),
    "update_list_record": ("list.rename", lambda db, who: _call(
        "update_list_record", db, user_id=who, list_id="L1", updates=["name = ?"], params=[NAME, "L1"])),
    "delete_list_record": ("list.delete", lambda db, who: _call("delete_list_record", db, user_id=who, list_id="L1")),
    "set_list_visibility_record": ("list.share", lambda db, who: _call(
        "set_list_visibility_record", db, user_id=who, list_id="L1", share=True)),
    "rename_item_on_list": ("item.edit", lambda db, who: _call(
        "rename_item_on_list", db, user_id=who, list_id="L1", old_text="wombel", new_text=WORD)),
    "add_items_to_list": ("item.add", lambda db, who: _call(
        "add_items_to_list", db, text=f"{WORD} and quince", user_id=who, list_type="shopping", list_name="Shopping",
        new_list_visibility="family")),
}


def pin_db():
    """The rows every pinned call starts from: the owner's own PRIVATE list with one item, so the share has a row to change."""
    return TxDB(visibility="personal", items=[{"id": "I1", "text": "wombel", "completed": 0}])


async def run_all() -> dict:
    out = {}
    for name in sorted(DOORS):
        db = pin_db()
        await DOORS[name][1](db, OWNER)
        out[name] = db.record()
    return out


def _isolated() -> None:
    """Refuse to run outside the test configuration: a list change reaches stores that default under the live ``~/.zoe`` (the
    shadow's log, the journals). ``tests/conftest.py`` points each at a temporary directory."""
    import os

    live = os.path.join(os.path.expanduser("~"), ".zoe")
    for var in ("ZOE_LOG_DIR", "MEMPALACE_DATA_DIR", "ZOE_MEMORY_REJECT_LEDGER"):
        value = os.environ.get(var, "")
        if not value or os.path.abspath(value).startswith(live):
            raise RuntimeError(f"lists door scenarios need the test configuration ({var} is not isolated): import tests/conftest.py first")
    if os.environ.get("ZOE_OS_LISTS", "").strip().lower() == "kernel":
        raise RuntimeError("lists door scenarios record the flag-off door: ZOE_OS_LISTS must not be 'kernel'")


if __name__ == "__main__":      # python3 <this file> <out.json>, with the CWD the zoe-data to record (an export of the older commit)
    import asyncio
    import sys

    sys.path[:0] = [".", "tests"]
    import conftest  # noqa: F401 - the CWD's test configuration: every live store path pinned to a temporary directory

    _isolated()
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(asyncio.run(run_all()), fh, indent=1, sort_keys=True)
        fh.write("\n")
