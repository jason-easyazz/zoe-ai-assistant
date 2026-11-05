"""A list the member NAMES is that list - never the household shopping list by default (QA round 3, 2026-11-06).

Live bug: "add renew <thing> to my personal to-do list" landed on the HOUSEHOLD shopping list: the fast-path regex captured
"personal to-do" as the list name and ``_normalize_list`` mapped every unrecognised name to "shopping". The fix resolves a
spoken name to ONE list type by its words, or to a NAMED list the executor must find among the member's own / household lists,
and otherwise ASKS - nothing is written. Same rule for remove and show, the slot extractor and the brain tool.

Real SQL against an in-memory SQLite (not scripted rows), so the visibility filters actually execute. All data is invented.
"""
import contextlib
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import expert_dispatch
import intent_router
import list_service
from intent_router import Intent, detect_intent

pytestmark = pytest.mark.ci_safe

ME = "casey"
OTHER = "robin"


class _Cur:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount

    async def fetchone(self):
        return self._cur.fetchone()

    async def fetchall(self):
        return self._cur.fetchall()


class SqlDb:
    def __init__(self):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.create_function("NOW", 0, lambda: "2026-11-06T00:00:00")
        self.con.executescript(
            """
            CREATE TABLE lists (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, list_type TEXT NOT NULL,
                description TEXT, visibility TEXT NOT NULL DEFAULT 'family', deleted INT DEFAULT 0,
                created_at TEXT DEFAULT '2026-10-01T00:00:00', updated_at TEXT DEFAULT '2026-10-01T00:00:00');
            CREATE TABLE list_items (id TEXT PRIMARY KEY, list_id TEXT NOT NULL, text TEXT NOT NULL, completed INT DEFAULT 0,
                priority TEXT DEFAULT 'normal', category TEXT, quantity TEXT, sort_order INT DEFAULT 0, parent_id TEXT,
                assigned_to TEXT, deleted INT DEFAULT 0, created_at TEXT DEFAULT '2026-10-01T00:00:00',
                updated_at TEXT DEFAULT '2026-10-01T00:00:00');
            """
        )

    async def execute(self, sql, params=()):
        return _Cur(self.con.execute(sql, tuple(params or ())))

    async def commit(self):
        pass

    def lst(self, lid, owner, name, ltype, vis):
        self.con.execute("INSERT INTO lists (id,user_id,name,list_type,visibility) VALUES (?,?,?,?,?)",
                         (lid, owner, name, ltype, vis))

    def item(self, iid, lid, text):
        self.con.execute("INSERT INTO list_items (id,list_id,text) VALUES (?,?,?)", (iid, lid, text))

    def open_items(self, lid):
        return [r["text"] for r in self.con.execute(
            "SELECT text FROM list_items WHERE list_id=? AND deleted=0 AND completed=0 ORDER BY rowid", (lid,))]

    def lists(self):
        return [dict(r) for r in self.con.execute("SELECT id, user_id, name, list_type, visibility FROM lists ORDER BY rowid")]


@pytest.fixture
def db(monkeypatch):
    import database

    d = SqlDb()
    d.lst("fam", OTHER, "Shopping", "shopping", "family")                # the household shopping list
    d.item("s1", "fam", "milk")
    d.lst("theirs", OTHER, "Personal", "personal", "family")             # ANOTHER member's shared to-do list
    d.item("t1", "theirs", "renew registration")

    @contextlib.asynccontextmanager
    async def _ctx():
        yield d

    async def _no_dup(*_a, **_k):
        return None        # the replay guard is Postgres-only SQL; not what is under test

    async def _notify(*_a, **_k):
        return None

    monkeypatch.setattr(database, "get_db_ctx", _ctx)
    monkeypatch.setattr(list_service, "_find_recent_duplicate", _no_dup)
    monkeypatch.setattr(intent_router, "_notify_lists_ui", _notify)
    return d


async def _say(text, user=ME):
    intent = detect_intent(text, log_miss=False)
    assert intent is not None and intent.name.startswith("list_"), (text, intent)
    executor = {"list_add": intent_router._execute_list_add_direct,
                "list_remove": intent_router._execute_list_remove_direct,
                "list_show": intent_router._execute_list_show_direct}[intent.name]
    return intent, await executor(intent, user)


def _mine(db, ltype="personal"):
    return [r for r in db.lists() if r["user_id"] == ME and r["list_type"] == ltype]


# ---- the parse: a spoken name is ONE type, or a named list - never a silent "shopping" ---------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("shopping", ("shopping", "")),
    ("groceries", ("shopping", "")),
    ("personal to-do", ("personal", "")),
    ("to do", ("personal", "")),
    ("own", ("personal", "")),
    ("private", ("personal", "")),
    ("tasks", ("tasks", "")),
    ("pharmacy", ("", "pharmacy")),
    ("personal shopping", ("", "personal shopping")),     # two types: ask, never guess the shared one
    ("", ("shopping", "")),                              # a bare "the list": the household list, as before
])
def test_list_target(raw, expected):
    assert intent_router._list_target(raw) == expected


def test_no_unknown_my_list_resolves_to_a_household_type():
    for name in ("pharmacy", "uni", "christmas", "secret santa", "study"):
        lt, named = intent_router._list_target(name)
        assert lt == "" and named == name


# ---- add -----------------------------------------------------------------------------------------------------------------------
async def test_add_to_the_shopping_list_still_goes_to_the_household_list(db):
    intent, out = await _say("add bread to the shopping list")
    assert intent.slots["list_type"] == "shopping"
    assert db.open_items("fam") == ["milk", "bread"] and "bread" in out.lower()


@pytest.mark.parametrize("text", [
    "add renew registration to my personal to-do list",
    "add renew registration to my to do list",
    "add renew registration to my to-do list",
    "add renew registration to my tasks",
])
async def test_my_todo_lands_on_the_members_own_list(db, text):
    _, out = await _say(text)
    assert db.open_items("fam") == ["milk"]                       # never the household shopping list
    assert db.open_items("theirs") == ["renew registration"]      # never another member's shared to-do list
    mine = [r for r in db.lists() if r["user_id"] == ME]
    assert len(mine) == 1 and mine[0]["visibility"] == "personal"
    assert db.open_items(mine[0]["id"]) == ["renew registration"]
    assert "shopping" not in out.lower()


async def test_my_todo_reuses_the_members_existing_list(db):
    db.lst("mine", ME, "My jobs", "personal", "personal")
    await _say("add call the plumber to my to-do list")
    assert db.open_items("mine") == ["call the plumber"]
    assert len(_mine(db)) == 1


async def test_unknown_named_list_asks_and_writes_nothing(db):
    before = db.lists()
    _, out = await _say("add renew registration to my pharmacy list")
    assert "pharmacy list" in out and "which list" in out.lower()
    assert db.lists() == before and db.open_items("fam") == ["milk"]


async def test_an_existing_named_list_is_used(db):
    db.lst("ph", ME, "Pharmacy", "personal", "personal")
    _, out = await _say("add renew registration to my pharmacy list")
    assert db.open_items("ph") == ["renew registration"] and db.open_items("fam") == ["milk"]
    assert "pharmacy" in out.lower()


async def test_an_exact_named_list_beats_the_type_default(db):
    db.lst("xmas", OTHER, "Christmas shopping", "shopping", "family")
    await _say("add tinsel to the christmas shopping list")
    assert db.open_items("xmas") == ["tinsel"] and db.open_items("fam") == ["milk"]


# ---- remove / show -------------------------------------------------------------------------------------------------------------
async def test_remove_from_an_unknown_named_list_touches_nothing(db):
    _, out = await _say("remove milk from my pharmacy list")
    assert db.open_items("fam") == ["milk"] and "pharmacy" in out


async def test_remove_from_my_todo_never_ticks_another_members_list(db):
    _, out = await _say("remove renew registration from my to-do list")
    assert db.open_items("theirs") == ["renew registration"]
    assert "wasn't on" in out


async def test_remove_from_the_shopping_list_still_works(db):
    await _say("remove milk from the shopping list")
    assert db.open_items("fam") == []


async def test_show_an_unknown_named_list_never_reads_the_shopping_list(db):
    _, out = await _say("show my pharmacy list")
    assert "milk" not in out.lower() and "pharmacy" in out


# ---- the brain tool / slot extractor / visibility rule ------------------------------------------------------------------------
def test_an_unknown_type_is_never_auto_created_family_visible():
    assert list_service.default_visibility("pharmacy") == "personal"
    assert list_service.default_visibility("shopping") == "family"


async def test_brain_tool_named_unknown_list_asks_and_writes_nothing(db, monkeypatch):
    import mcp_server

    async def _actor(*_a, **_k):
        return {"user_id": ME}

    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", _actor)
    before = db.lists()
    out = await mcp_server._execute_tool(db, "list_add_item", {"list_type": "shopping", "list_name": "pharmacy",
                                                               "text": "renew registration"})
    assert "error" in out and db.lists() == before and db.open_items("fam") == ["milk"]
    # negative control: a typed name still adds (to the household shopping list)
    out = await mcp_server._execute_tool(db, "list_add_item", {"list_type": "shopping", "list_name": "Shopping",
                                                               "text": "bread"})
    assert "error" not in out and db.open_items("fam") == ["milk", "bread"]


async def test_slot_extractor_honours_the_named_list(monkeypatch):
    import nlu_extractor

    async def _model(_text, _schema):
        return {"item": "renew registration", "list_type": "shopping"}       # the enum's guess

    monkeypatch.setattr(nlu_extractor, "_call_with_tool", _model)
    slots = await nlu_extractor._extract_list_add("uh could you pop renew registration onto my pharmacy list")
    assert slots["list_type"] == "" and slots["list_name"] == "pharmacy"
    slots = await nlu_extractor._extract_list_add("pop renew registration on my personal to-do list")
    assert slots["list_type"] == "personal"


# ---- finding 2: a unit conversion is not weather ------------------------------------------------------------------------------
@pytest.mark.parametrize("text,conv", [
    ("375 fahrenheit? we're in australia haha, whats that in celsius", True),
    ("convert 180 celsius to fahrenheit", True),
    ("what's the weather in celsius today", False),
    ("is it going to rain tomorrow", False),
])
def test_temperature_conversion_is_not_weather(text, conv):
    assert expert_dispatch._is_unit_conversion(text) is conv
