"""Household lists work like a fridge list: ANYONE AT THE PANEL adds, ticks off and removes ITEMS (2026-10-15).

Live bug: "there are two shopping lists and I can't delete anything from the one that has items". The panel is a kiosk guest;
the REST layer refused every guest item write (403), and a guest/second-member "add milk" that found no list of its own
created another one. Owner decision: a household (family-visible) list's ITEMS are open to anyone at the panel; the LIST itself
(rename/delete) still needs a signed-in member; a private list stays private; an anonymous caller who is not the panel gets
nothing. All data here is invented.

Real SQL against an in-memory SQLite (not scripted rows), so the visibility filters actually execute.
"""
import contextlib
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import intent_router
import list_service
import panel_principal
from models import ListItemCreate, ListItemUpdate, ListUpdate
from routers import lists

pytestmark = pytest.mark.ci_safe

PANEL_IP = "192.0.2.50"      # TEST-NET-1: the registered panel
OTHER_IP = "192.0.2.77"      # a phone on the same Wi-Fi
GUEST = {"user_id": "guest", "role": "guest", "username": "guest"}
MEMBER = {"user_id": "casey", "role": "user", "username": "casey"}


class _Cur:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount

    async def fetchone(self):
        return self._cur.fetchone()

    async def fetchall(self):
        return self._cur.fetchall()


class SqlDb:
    """The AsyncpgCompat surface the routers use (``execute(sql, params)`` + ``?`` placeholders), over SQLite."""

    def __init__(self):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.create_function("NOW", 0, lambda: "2026-10-15T00:00:00")
        self.con.executescript(
            """
            CREATE TABLE panels (panel_id TEXT PRIMARY KEY, ip_address TEXT, is_active INT DEFAULT 1, allow_guest INT DEFAULT 1);
            CREATE TABLE lists (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, list_type TEXT NOT NULL,
                description TEXT, visibility TEXT NOT NULL DEFAULT 'family', deleted INT DEFAULT 0,
                created_at TEXT DEFAULT '2026-10-01T00:00:00', updated_at TEXT DEFAULT '2026-10-01T00:00:00');
            CREATE TABLE list_items (id TEXT PRIMARY KEY, list_id TEXT NOT NULL, text TEXT NOT NULL, completed INT DEFAULT 0,
                priority TEXT DEFAULT 'normal', category TEXT, quantity TEXT, sort_order INT DEFAULT 0, parent_id TEXT,
                assigned_to TEXT, deleted INT DEFAULT 0, created_at TEXT DEFAULT '2026-10-01T00:00:00',
                updated_at TEXT DEFAULT '2026-10-01T00:00:00');
            """
        )
        self.commits = 0

    async def execute(self, sql, params=()):
        return _Cur(self.con.execute(sql, tuple(params or ())))

    async def commit(self):
        self.commits += 1

    # fixtures -----------------------------------------------------------------------------------------------------------
    def panel(self, ip=PANEL_IP, active=1, allow_guest=1):
        self.con.execute("INSERT INTO panels VALUES ('kitchen-panel', ?, ?, ?)", (ip, active, allow_guest))

    def lst(self, lid, owner, name="Shopping", ltype="shopping", vis="family", created="2026-10-01T00:00:00"):
        self.con.execute("INSERT INTO lists (id,user_id,name,list_type,visibility,created_at) VALUES (?,?,?,?,?,?)",
                         (lid, owner, name, ltype, vis, created))

    def item(self, iid, lid, text, completed=0):
        self.con.execute("INSERT INTO list_items (id,list_id,text,completed) VALUES (?,?,?,?)", (iid, lid, text, completed))

    def live_items(self, lid):
        return [r["text"] for r in self.con.execute(
            "SELECT text FROM list_items WHERE list_id=? AND deleted=0 ORDER BY rowid", (lid,))]

    def list_ids(self, ltype="shopping"):
        return [r["id"] for r in self.con.execute("SELECT id FROM lists WHERE list_type=? AND deleted=0", (ltype,))]


def _request(peer="127.0.0.1", real_ip=None, session=None) -> Request:
    headers = []
    if real_ip:
        headers.append((b"x-real-ip", real_ip.encode()))
    if session:
        headers.append((b"x-session-id", session.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (peer, 1234)})


def _panel_request():
    """What nginx hands zoe-data for the kiosk: peer = our proxy, X-Real-IP = the panel's address."""
    return _request(peer="127.0.0.1", real_ip=PANEL_IP, session="guest-session")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE", raising=False)
    monkeypatch.delenv("ZOE_TRUSTED_PROXY_NETS", raising=False)

    async def _bcast(*_a, **_k):
        return 1

    monkeypatch.setattr(lists.broadcaster, "broadcast", _bcast)


@pytest.fixture
def db():
    d = SqlDb()
    d.panel()
    d.lst("fam", "jo", name="Shopping")                       # the household list
    d.item("i1", "fam", "milk")
    d.item("i2", "fam", "eggs")
    d.lst("priv", "jo", name="My secrets", ltype="personal", vis="personal")
    d.item("p1", "priv", "surprise gift")
    d.lst("gpriv", "guest", name="Guest stuff", ltype="personal", vis="personal")   # a guest-session personal list
    d.item("g1", "gpriv", "stray")
    return d


# ---- REST: the panel guest -------------------------------------------------------------------------------------------------
async def test_panel_guest_can_add_tick_and_remove_items_on_a_household_list(db):
    added = await lists.add_item("shopping", "fam", ListItemCreate(text="bread"), user=GUEST, db=db, request=_panel_request())
    assert added["text"] == "bread"
    ticked = await lists.update_item("shopping", "fam", "i1", ListItemUpdate(completed=True), user=GUEST, db=db,
                                     request=_panel_request())
    assert ticked["completed"] == 1
    out = await lists.delete_item("shopping", "fam", "i2", user=GUEST, db=db, request=_panel_request())
    assert out == {"ok": True}
    assert db.live_items("fam") == ["milk", "bread"]


async def test_panel_guest_cannot_rename_or_delete_the_list_itself(db):
    with pytest.raises(HTTPException) as e1:
        await lists.delete_list("shopping", "fam", user=GUEST, db=db)
    assert e1.value.status_code == 403
    with pytest.raises(HTTPException) as e2:
        await lists.update_list("shopping", "fam", ListUpdate(name="Renamed"), user=GUEST, db=db)
    assert e2.value.status_code == 403
    assert db.list_ids() == ["fam"]


async def test_panel_guest_never_sees_or_changes_a_private_list(db):
    req = _panel_request()
    for lid, ltype in (("priv", "personal"), ("gpriv", "personal")):
        with pytest.raises(HTTPException) as e:
            await lists.get_list(ltype, lid, user=GUEST, db=db)
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            await lists.add_item(ltype, lid, ListItemCreate(text="x"), user=GUEST, db=db, request=req)
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            await lists.delete_item(ltype, lid, "p1" if lid == "priv" else "g1", user=GUEST, db=db, request=req)
        assert e.value.status_code == 404
    assert (await lists.list_lists("personal", user=GUEST, db=db))["lists"] == []
    assert db.live_items("priv") == ["surprise gift"] and db.live_items("gpriv") == ["stray"]


async def test_a_guest_session_does_not_own_the_lists_it_once_created(db):
    """Negative control for the viewer filter: `user_id = 'guest'` used to match a guest-owned personal list."""
    seen = await lists.list_lists("personal", user=GUEST, db=db)
    assert seen["lists"] == []
    # ...while the signed-in owner still sees theirs.
    jo = await lists.list_lists("personal", user={"user_id": "jo", "role": "user"}, db=db)
    assert [row["id"] for row in jo["lists"]] == ["priv"]


@pytest.mark.parametrize("op", ["add", "update", "delete"])
async def test_a_guest_that_is_not_the_panel_is_refused(db, op):
    """Anonymous LAN caller: no request at all, an unregistered address, or a spoofed X-Real-IP from a direct peer."""
    calls = {
        "add": lambda r: lists.add_item("shopping", "fam", ListItemCreate(text="x"), user=GUEST, db=db, request=r),
        "update": lambda r: lists.update_item("shopping", "fam", "i1", ListItemUpdate(completed=True), user=GUEST, db=db, request=r),
        "delete": lambda r: lists.delete_item("shopping", "fam", "i1", user=GUEST, db=db, request=r),
    }
    for req in (
        None,                                                                    # a direct call is never the panel
        _request(peer="127.0.0.1", real_ip=OTHER_IP, session="s"),              # a phone, through nginx
        _request(peer=OTHER_IP, real_ip=PANEL_IP, session="s"),                  # direct to :8000, spoofing the header
        _request(peer="127.0.0.1", session="s"),                                 # no address evidence at all
    ):
        with pytest.raises(HTTPException) as e:
            await calls[op](req)
        assert e.value.status_code == 403
    assert db.live_items("fam") == ["milk", "eggs"]


async def test_negative_control_the_same_request_from_the_registered_panel_is_allowed(db):
    """The refusals above are about WHERE the request comes from, not an over-broad block."""
    out = await lists.delete_item("shopping", "fam", "i1", user=GUEST, db=db, request=_panel_request())
    assert out == {"ok": True}
    # and a direct peer that IS the panel (no proxy in between) is judged by its own address
    out = await lists.delete_item("shopping", "fam", "i2", user=GUEST, db=db, request=_request(peer=PANEL_IP))
    assert out == {"ok": True}


@pytest.mark.parametrize("active,allow_guest", [(0, 1), (1, 0)])
async def test_a_deactivated_or_guest_refusing_panel_is_not_the_panel(active, allow_guest):
    d = SqlDb()
    d.panel(active=active, allow_guest=allow_guest)
    d.lst("fam", "jo")
    d.item("i1", "fam", "milk")
    with pytest.raises(HTTPException) as e:
        await lists.delete_item("shopping", "fam", "i1", user=GUEST, db=d, request=_panel_request())
    assert e.value.status_code == 403


async def test_a_device_token_guest_is_the_panel_without_an_address_lookup():
    d = SqlDb()          # NO panel registered: the provisioned device token is the evidence
    d.lst("fam", "jo")
    d.item("i1", "fam", "milk")
    token_guest = {**GUEST, "panel_id": "kitchen-panel", "device_token_role": "voice"}
    out = await lists.delete_item("shopping", "fam", "i1", user=token_guest, db=d, request=_request(peer=OTHER_IP))
    assert out == {"ok": True}


async def test_an_auth_outage_guest_is_never_evidence(db):
    degraded = {**GUEST, "auth_degraded": True}
    with pytest.raises(HTTPException) as e:
        await lists.delete_item("shopping", "fam", "i1", user=degraded, db=db, request=_panel_request())
    assert e.value.status_code == 403


async def test_scope_off_restores_the_old_rule(db, monkeypatch):
    monkeypatch.setenv("ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE", "off")
    with pytest.raises(HTTPException) as e:
        await lists.delete_item("shopping", "fam", "i1", user=GUEST, db=db, request=_panel_request())
    assert e.value.status_code == 403


async def test_scope_lan_needs_a_private_address_and_a_session(db, monkeypatch):
    monkeypatch.setenv("ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE", "lan")
    d = SqlDb()          # no panel registered at all
    d.lst("fam", "jo")
    d.item("i1", "fam", "milk")
    d.item("i2", "fam", "eggs")
    lan = _request(peer="127.0.0.1", real_ip="192.168.1.20", session="s")
    assert (await lists.delete_item("shopping", "fam", "i1", user=GUEST, db=d, request=lan)) == {"ok": True}
    for req in (_request(peer="127.0.0.1", real_ip="192.168.1.20"),              # no session
                _request(peer="127.0.0.1", real_ip="8.8.8.8", session="s")):  # not a private address
        with pytest.raises(HTTPException) as e:
            await lists.delete_item("shopping", "fam", "i2", user=GUEST, db=d, request=req)
        assert e.value.status_code == 403


# ---- REST: members, and the list itself ------------------------------------------------------------------------------------
async def test_a_member_can_remove_a_guest_created_duplicate_list_but_not_another_members_list(db):
    db.lst("dup", "guest", name="Shopping")              # the stray a guest session once created
    db.lst("hh", "family-admin", name="Shopping 2")      # a household-owned list
    db.lst("theirs", "jo", name="Jo's family list")     # a real member's list: unchanged rule
    assert (await lists.delete_list("shopping", "dup", user=MEMBER, db=db)) == {"ok": True}
    assert (await lists.delete_list("shopping", "hh", user=MEMBER, db=db)) == {"ok": True}
    with pytest.raises(HTTPException) as e:
        await lists.delete_list("shopping", "theirs", user=MEMBER, db=db)
    assert e.value.status_code == 403


async def test_the_panel_presents_one_shopping_list(db):
    db.lst("dup", "guest", name="Shopping", vis="family", created="2026-10-14T00:00:00")   # empty family twin
    ids = [row["id"] for row in (await lists.list_lists("shopping", user=GUEST, db=db))["lists"]]
    assert ids == ["fam"]
    # a twin that HOLDS items is never hidden (that would hide data) - the merge script is what combines it
    db.item("d1", "dup", "bananas")
    ids = {row["id"] for row in (await lists.list_lists("shopping", user=GUEST, db=db))["lists"]}
    assert ids == {"fam", "dup"}
    # and an empty list with a DIFFERENT name is a deliberate list, not a duplicate
    db.lst("other", "jo", name="Hardware run")
    ids = {row["id"] for row in (await lists.list_lists("shopping", user=MEMBER, db=db))["lists"]}
    assert "other" in ids


# ---- the shared writers: the household list, never a new guest list --------------------------------------------------------
def _pool(monkeypatch, database_under_test):
    import database

    @contextlib.asynccontextmanager
    async def _ctx():
        yield database_under_test

    async def _no_dup(*_a, **_k):
        return None        # the replay guard is Postgres-only SQL (::timestamptz); not what is under test

    monkeypatch.setattr(database, "get_db_ctx", _ctx)
    monkeypatch.setattr(list_service, "_find_recent_duplicate", _no_dup)
    return database_under_test


@pytest.fixture
def pooled(db, monkeypatch):
    return _pool(monkeypatch, db)


@pytest.fixture
def empty_pooled(monkeypatch):
    return _pool(monkeypatch, SqlDb())


async def test_a_guest_voice_add_lands_on_the_household_list(pooled):
    before = pooled.list_ids()
    panel_principal.mark_panel_turn()
    out = await intent_router.execute_intent(intent_router.Intent("list_add", {"item": "oat milk", "list_type": "shopping"}), "guest")
    assert "oat milk" in out.lower()
    assert pooled.list_ids() == before                     # NO second shopping list
    assert pooled.live_items("fam")[-1] == "oat milk"
    # it did not go to the guest-owned personal list either
    assert pooled.live_items("gpriv") == ["stray"]


async def test_a_guest_voice_add_starts_ONE_household_shopping_list_when_there_is_none(empty_pooled):
    d = empty_pooled
    panel_principal.mark_panel_turn()
    await intent_router.execute_intent(intent_router.Intent("list_add", {"item": "rice", "list_type": "shopping"}), "guest")
    await intent_router.execute_intent(intent_router.Intent("list_add", {"item": "salt", "list_type": "shopping"}), "voice-guest")
    rows = d.con.execute("SELECT id, user_id, visibility FROM lists").fetchall()
    assert len(rows) == 1
    assert rows[0]["visibility"] == "family" and rows[0]["user_id"] == panel_principal.HOUSEHOLD_OWNER_ID
    assert d.live_items(rows[0]["id"]) == ["rice", "salt"]


async def test_a_guest_cannot_conjure_a_household_list_of_another_type(pooled):
    panel_principal.mark_panel_turn()
    out = await intent_router.execute_intent(intent_router.Intent("list_add", {"item": "x", "list_type": "tasks"}), "guest")
    assert "shopping" in out.lower() and "isn't a household" in out.lower()
    assert pooled.list_ids("tasks") == []


async def test_without_a_panel_turn_a_guest_voice_or_chat_add_is_refused_and_writes_nothing(pooled):
    out = await intent_router.execute_intent(intent_router.Intent("list_add", {"item": "oat milk", "list_type": "shopping"}), "guest")
    assert "who you are" in out.lower()
    assert pooled.live_items("fam") == ["milk", "eggs"]


async def test_a_guest_reads_and_ticks_off_the_household_list_but_never_a_private_one(pooled):
    panel_principal.mark_panel_turn()
    shown = await intent_router.execute_intent(intent_router.Intent("list_show", {"list_type": "shopping"}), "guest")
    assert "milk" in shown and "eggs" in shown
    personal = await intent_router.execute_intent(intent_router.Intent("list_show", {"list_type": "personal"}), "guest")
    assert "surprise gift" not in personal and "stray" not in personal
    removed = await intent_router.execute_intent(intent_router.Intent("list_remove", {"item": "milk", "list_type": "shopping"}), "guest")
    assert "milk" in removed.lower()
    assert pooled.con.execute("SELECT completed FROM list_items WHERE id='i1'").fetchone()["completed"] == 1
    # a private list's item is out of reach even by name
    gone = await intent_router.execute_intent(intent_router.Intent("list_remove", {"item": "surprise gift", "list_type": "personal"}), "guest")
    assert "wasn't on" in gone.lower()
    assert pooled.con.execute("SELECT completed FROM list_items WHERE id='p1'").fetchone()["completed"] == 0


def test_the_next_members_add_converges_on_the_household_list():
    """The duplicate's root cause: shopping auto-created as the first speaker's PERSONAL list."""
    assert list_service.default_visibility("shopping") == "family"
    assert list_service.default_visibility("tasks") == "personal"


async def test_panel_turn_marks_do_not_leak_between_requests():
    import asyncio

    async def _a():
        panel_principal.mark_panel_turn()
        return panel_principal.in_panel_turn()

    async def _b():
        await asyncio.sleep(0)
        return panel_principal.in_panel_turn()

    # separate tasks = separate contexts (how the server runs two requests)
    assert await asyncio.create_task(_a()) is True
    assert await asyncio.create_task(_b()) is False


def test_trusted_client_ip_ignores_x_real_ip_from_a_direct_peer():
    assert panel_principal.trusted_client_ip(_request(peer="192.0.2.77", real_ip="192.0.2.50")) == "192.0.2.77"
    assert panel_principal.trusted_client_ip(_request(peer="127.0.0.1", real_ip="192.0.2.50")) == "192.0.2.50"
    assert panel_principal.trusted_client_ip(_request(peer="172.18.0.4", real_ip="192.0.2.50")) == "192.0.2.50"
    assert panel_principal.household_actor(GUEST) == "panel" and panel_principal.household_actor(MEMBER) == "casey"


async def test_a_request_that_crossed_cloudflare_is_never_the_panel(db):
    """The tunnel's container address is inside the trusted proxy range and Cloudflare passes a caller's own X-Real-IP through."""
    for header in (b"cf-connecting-ip", b"cf-ray", b"cf-visitor"):
        req = Request({"type": "http", "method": "GET", "path": "/", "client": ("172.18.0.9", 1),
                       "headers": [(b"x-real-ip", PANEL_IP.encode()), (b"x-session-id", b"s"), (header, b"1")]})
        with pytest.raises(HTTPException) as e:
            await lists.delete_item("shopping", "fam", "i1", user=GUEST, db=db, request=req)
        assert e.value.status_code == 403
    # negative control: the same request without the Cloudflare header, from the docker proxy range, IS the panel
    ok = Request({"type": "http", "method": "GET", "path": "/", "client": ("172.18.0.9", 1),
                  "headers": [(b"x-real-ip", PANEL_IP.encode()), (b"x-session-id", b"s")]})
    assert (await lists.delete_item("shopping", "fam", "i1", user=GUEST, db=db, request=ok)) == {"ok": True}


async def test_pinning_the_proxy_address_narrows_who_may_vouch_for_x_real_ip(db, monkeypatch):
    monkeypatch.setenv("ZOE_TRUSTED_PROXY_NETS", "127.0.0.0/8,172.18.0.5/32")
    sibling = Request({"type": "http", "method": "GET", "path": "/", "client": ("172.18.0.9", 1),
                       "headers": [(b"x-real-ip", PANEL_IP.encode()), (b"x-session-id", b"s")]})
    with pytest.raises(HTTPException) as e:
        await lists.delete_item("shopping", "fam", "i1", user=GUEST, db=db, request=sibling)
    assert e.value.status_code == 403
    nginx = Request({"type": "http", "method": "GET", "path": "/", "client": ("172.18.0.5", 1),
                     "headers": [(b"x-real-ip", PANEL_IP.encode()), (b"x-session-id", b"s")]})
    assert (await lists.delete_item("shopping", "fam", "i1", user=GUEST, db=db, request=nginx)) == {"ok": True}


async def test_the_household_list_is_the_fullest_then_the_OLDEST_never_the_most_recently_touched(db):
    # equal fullness, different age: the older one wins; touching the newer one does not change that
    db.lst("older", "jo", name="Pantry", ltype="bucket", created="2026-09-01T00:00:00")
    db.lst("newer", "jo", name="Pantry", ltype="bucket", created="2026-10-09T00:00:00")
    db.item("o1", "older", "a")
    db.item("n1", "newer", "b")
    db.con.execute("UPDATE lists SET updated_at='2026-10-15T09:00:00' WHERE id='newer'")
    assert await list_service.find_household_list_id(db, list_type="bucket", name="Pantry") == "older"
    # fuller beats older
    db.item("n2", "newer", "c")
    assert await list_service.find_household_list_id(db, list_type="bucket", name="Pantry") == "newer"


def test_the_skybridge_household_resolvers_rank_fullest_then_oldest_not_most_recent():
    import inspect
    import skybridge_service

    for fn in (skybridge_service._find_or_create_list, skybridge_service._find_list_for_remove):
        src = inspect.getsource(fn)
        order = src[src.rindex("ORDER BY"):]
        assert "COUNT(*)" in order and "created_at ASC" in order, fn.__name__
        assert "updated_at DESC" not in order, fn.__name__


# ---- over real HTTP: FastAPI really injects the Request into the optional `request` parameter ---------------------------
def _client(db, user):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(lists.router)

    async def _db():
        yield db

    app.dependency_overrides[lists.get_db] = _db
    app.dependency_overrides[lists.get_current_user] = lambda: user
    # the TCP peer is nginx on the same host; the browser's address arrives in X-Real-IP
    return TestClient(app, client=("127.0.0.1", 50000))


def test_http_the_kiosk_deletes_an_item_but_not_the_list_and_a_phone_deletes_nothing(db):
    kiosk = _client(db, GUEST)
    assert kiosk.delete("/api/lists/shopping/fam/items/i1", headers={"X-Real-IP": PANEL_IP}).status_code == 200
    assert kiosk.delete("/api/lists/shopping/fam", headers={"X-Real-IP": PANEL_IP}).status_code == 403
    assert kiosk.put("/api/lists/shopping/fam", json={"name": "x"}, headers={"X-Real-IP": PANEL_IP}).status_code == 403
    assert kiosk.delete("/api/lists/shopping/fam/items/i2", headers={"X-Real-IP": OTHER_IP}).status_code == 403
    assert kiosk.delete("/api/lists/shopping/fam/items/i2").status_code == 403          # no address evidence
    assert kiosk.post("/api/lists/shopping/fam/items", json={"text": "tea"}, headers={"X-Real-IP": PANEL_IP}).status_code == 200
    assert kiosk.post("/api/lists/shopping", json={"name": "Another"}, headers={"X-Real-IP": PANEL_IP}).status_code == 403
    assert db.live_items("fam") == ["eggs", "tea"]
