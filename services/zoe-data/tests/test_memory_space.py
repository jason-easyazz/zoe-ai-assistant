"""The touch panel's Memories space: GET /api/memories/mine and POST /api/memories/mine/{id}/forget.

The page only renders what the server gives it, so the server is where "an unrecognised caller never sees private data" is
decided. One group per promise, each with a break-the-fix control (the wall removed -> the test goes red):

  identity   guest / guest-shaped id / no session header (device token) / degraded auth -> 401/403 and NO rows
  own rows   a member sees their rows and never another member's, with no parameter that could ask for them
  private    health / money / feelings rows are counted, their words never leave the server
  shape      groups (people merged, preferences, routines, dates, other), provenance badge, bounded
  forget     own row forgotten with its twin; another member's row, an absent row and an already-forgotten row all answer 404
             and the other member's row is untouched; a guest cannot forget

Synthetic data only (``demo_bar_`` ids and invented names), fake Chroma (``ci_safe``).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import memory_service
import memory_space
from database import get_db
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

ME = "demo_bar_00000001"
OTHER = "demo_bar_00000002"
H = {"X-Session-ID": "sess-member"}
ME_USER = {"user_id": ME, "role": "user", "username": "casey"}

# Distinct, searchable tokens: if one of OTHER's ever appears in a response for ME (or for a guest), the leak is plain.
OTHER_SECRET = "Zibblewort"
ME_SISTER = "User's sister Marisol is flying in from Lisbon on Thursday"
ME_LIKE = "I like dark roast coffee with oat milk."
ME_ROUTINE = "I walk the dog every morning before work."
ME_BIRTHDAY = "My birthday is on 14 March."
ME_HEALTH = "I have migraines most weeks and my doctor wants a scan."
ME_OTHER = "I keep my bike in the shed behind the garage."


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)


@pytest.fixture
def svc(monkeypatch):
    s = MemoryService(data_dir="/nonexistent/zoe-test-memory-space")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    from routers import memories

    monkeypatch.setattr(memories, "_svc", lambda: s)
    s._col = col
    return s


def put(svc, text, *, user=ME, **kw):
    return run(svc.ingest(text, user_id=user, source="chat_regex", confidence=0.9, session_id="s0",
                          captured_at="2026-10-06T02:00:00Z", **kw))


@pytest.fixture
def seeded(svc):
    for t in (ME_SISTER, ME_LIKE, ME_ROUTINE, ME_BIRTHDAY, ME_HEALTH, ME_OTHER):
        put(svc, t)
    put(svc, f"My neighbour {OTHER_SECRET} keeps a parrot called Biscuit.", user=OTHER)
    put(svc, f"I have {OTHER_SECRET} disease and I take insulin every day.", user=OTHER)
    return svc


def make_client(current_user):
    """The real router; ``get_current_user`` replaced by whatever the session resolved to (the server's job, faked here)."""
    from auth import get_current_user
    from routers.memories import router

    app = FastAPI()
    app.include_router(router)

    async def _user(request: Request):
        return current_user(request)

    async def _no_db():
        yield None

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_db] = _no_db
    return TestClient(app)


def as_member(user=ME_USER):
    return make_client(lambda _r: dict(user))


GUEST = {"user_id": "guest", "role": "guest", "username": "guest", "permissions": []}


def flat(resp) -> str:
    return resp.text


# ── identity ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("user", [
    GUEST,
    {"user_id": "guest-3f9a1c", "role": "guest"},
    {"user_id": "guest-3f9a1c", "role": "user"},        # a guest-shaped id with a real-looking role is still a guest
    {"user_id": "anonymous", "role": "user"},
    {"user_id": "voice-guest", "role": "user"},
    {"user_id": ME, "role": "guest"},                   # a downgraded member (session fell to guest)
    {"user_id": "", "role": "user"},
])
def test_a_guest_never_gets_a_row(seeded, user):
    r = as_member(user).get("/api/memories/mine", headers=H)
    assert r.status_code in (401, 403), r.text
    body = flat(r)
    for needle in ("Marisol", "coffee", "migraine", OTHER_SECRET, "groups", "private"):
        assert needle not in body, f"{needle!r} leaked to a guest"


def test_no_session_header_is_refused_even_if_the_server_resolved_a_member(seeded):
    """A device token resolves to the panel's bound user without X-Session-ID: that standing binding is not a person."""
    r = as_member().get("/api/memories/mine")           # no header
    assert r.status_code == 401
    assert "Marisol" not in flat(r)


def test_degraded_auth_is_refused(seeded):
    r = as_member({**ME_USER, "auth_degraded": True}).get("/api/memories/mine", headers=H)
    assert r.status_code == 401
    assert "Marisol" not in flat(r)


def test_the_unauthenticated_default_user_is_refused(seeded):
    """What get_current_user returns for a request with no session at all."""
    r = make_client(lambda _r: dict(GUEST)).get("/api/memories/mine")
    assert r.status_code == 401


# ── own rows only ─────────────────────────────────────────────────────────────────────────────

def test_a_member_sees_their_own_rows_and_never_anothers(seeded):
    r = as_member().get("/api/memories/mine", headers=H)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = flat(r)
    assert "dark roast" in body and "Marisol" in body
    assert OTHER_SECRET not in body and "Biscuit" not in body


def test_the_other_member_sees_only_theirs(seeded):
    r = as_member({"user_id": OTHER, "role": "user"}).get("/api/memories/mine", headers=H)
    assert r.status_code == 200
    body = flat(r)
    assert "Biscuit" in body
    assert "Marisol" not in body and "dark roast" not in body


def test_there_is_no_parameter_that_names_another_member(seeded):
    """?user_id= / ?member= / ?as= are ignored: the identity is the session's, nothing else."""
    for q in (f"user_id={OTHER}", f"member={OTHER}", f"as={OTHER}", f"wing={OTHER}"):
        r = as_member().get(f"/api/memories/mine?{q}", headers=H)
        assert r.status_code == 200
        assert "Biscuit" not in flat(r) and OTHER_SECRET not in flat(r), q
    from routers import memories

    import inspect
    assert "user_id" not in inspect.signature(memories.my_memories).parameters


# ── private classes are counted, never read ─────────────────────────────────────────────────────

def test_private_rows_are_counted_not_read(seeded):
    r = as_member().get("/api/memories/mine", headers=H)
    data = r.json()
    assert data["private"].get("health", 0) >= 1, data["private"]
    body = flat(r)
    assert "migraine" not in body.lower() and "scan" not in body.lower() and "doctor" not in body.lower()
    # and not as a "text" of any item either
    for g in data["groups"]:
        for it in g.get("items", []):
            assert "migraine" not in it["text"].lower()


# ── shape ─────────────────────────────────────────────────────────────────────────────────────

def test_groups_and_provenance(seeded):
    data = as_member().get("/api/memories/mine", headers=H).json()
    keys = [g["key"] for g in data["groups"]]
    assert "people" in keys and "preferences" in keys and "routines" in keys and "dates" in keys
    assert keys == sorted(keys, key=[k for k, _ in memory_space.GROUP_LABELS].index), "display order"
    people = next(g for g in data["groups"] if g["key"] == "people")
    assert people["kind"] == "people" and any("Marisol" in (e["name"] or e["label"]) for e in people["entries"])
    assert all("id" not in e for e in people["entries"]), "a person is forgotten by voice, not by a row id"
    dates = next(g for g in data["groups"] if g["key"] == "dates")
    assert any("14 March" in i["text"] for i in dates["items"])
    prefs = next(g for g in data["groups"] if g["key"] == "preferences")
    item = prefs["items"][0]
    assert item["source"] in ("told", "inferred") and item["id"] and item["when"]
    assert item["text"].lower().startswith("you ") or "coffee" in item["text"].lower()
    # the header count is what the screen LISTS: the held-back private row is not in it
    listed = sum(len(g.get("entries") or g.get("items") or []) + g["more"] for g in data["groups"])
    assert data["total"] == listed and data["private"].get("health", 0) >= 1


def test_groups_are_bounded(svc):
    for i in range(memory_space.GROUP_MAX_ITEMS + 7):
        put(svc, f"I keep gadget number {i} in the shed behind the garage in box {i}.")
    data = as_member().get("/api/memories/mine", headers=H).json()
    other = next(g for g in data["groups"] if g["key"] == "other")
    assert len(other["items"]) == memory_space.GROUP_MAX_ITEMS and other["more"] >= 7


def test_nothing_known_is_an_empty_list_not_an_error(svc):
    r = as_member().get("/api/memories/mine", headers=H)
    assert r.status_code == 200 and r.json()["groups"] == []


def test_a_failed_store_read_is_503_not_nothing(seeded, monkeypatch):
    import provenance_answers as pa

    async def boom(*_a, **_k):
        raise RuntimeError("store down")

    monkeypatch.setattr(pa, "gather", boom)
    r = as_member().get("/api/memories/mine", headers=H)
    assert r.status_code == 503


# ── forget ────────────────────────────────────────────────────────────────────────────────────

def approved_texts(svc, user):
    return [r.text for r in run(svc.list_by_status(user_id=user, status="approved", limit=500))]


def row_id(svc, user, needle):
    return next(r.id for r in run(svc.list_by_status(user_id=user, status="approved", limit=500)) if needle in r.text)


def test_forget_own_row(seeded):
    rid = row_id(seeded, ME, "dark roast")
    c = as_member()
    r = c.post(f"/api/memories/mine/{rid}/forget", headers=H)
    assert r.status_code == 200 and r.json() == {"forgotten": True}
    assert not any("dark roast" in t for t in approved_texts(seeded, ME))
    assert "dark roast" not in flat(c.get("/api/memories/mine", headers=H))
    # the member's other rows are untouched
    assert any("Marisol" in t for t in approved_texts(seeded, ME))


def test_forget_takes_the_twin_the_same_utterance_left(svc):
    put(svc, "I like dark roast coffee with oat milk.", source_excerpt="I like dark roast coffee with oat milk please.")
    put(svc, "User likes dark roast coffee with oat milk.", source_excerpt="I like dark roast coffee with oat milk please.")
    assert len(approved_texts(svc, ME)) == 2 or len(approved_texts(svc, ME)) == 1   # the ingest gate may already merge them
    rid = row_id(svc, ME, "oat milk")
    r = as_member().post(f"/api/memories/mine/{rid}/forget", headers=H)
    assert r.status_code == 200
    assert not any("oat milk" in t for t in approved_texts(svc, ME)), "a twin must not keep the fact alive"


def test_forget_another_members_row_is_404_and_changes_nothing(seeded):
    other_id = row_id(seeded, OTHER, "Biscuit")
    r = as_member().post(f"/api/memories/mine/{other_id}/forget", headers=H)
    assert r.status_code == 404
    assert any("Biscuit" in t for t in approved_texts(seeded, OTHER)), "the other member's row must survive"
    # the answer is the same as for a row that never existed: no oracle
    absent = as_member().post("/api/memories/mine/does-not-exist/forget", headers=H)
    assert absent.status_code == 404 and absent.json() == r.json()


def test_forget_twice_is_404(seeded):
    rid = row_id(seeded, ME, "dark roast")
    c = as_member()
    assert c.post(f"/api/memories/mine/{rid}/forget", headers=H).status_code == 200
    assert c.post(f"/api/memories/mine/{rid}/forget", headers=H).status_code == 404


@pytest.mark.parametrize("user", [GUEST, {"user_id": "guest-3f9a1c", "role": "user"}, {"user_id": ME, "role": "guest"}])
def test_a_guest_cannot_forget(seeded, user):
    rid = row_id(seeded, ME, "dark roast")
    r = as_member(user).post(f"/api/memories/mine/{rid}/forget", headers=H)
    assert r.status_code in (401, 403)
    assert any("dark roast" in t for t in approved_texts(seeded, ME)), "a guest's forget must not touch the row"


def test_forget_without_a_session_header_is_401(seeded):
    rid = row_id(seeded, ME, "dark roast")
    r = as_member().post(f"/api/memories/mine/{rid}/forget")
    assert r.status_code == 401
    assert any("dark roast" in t for t in approved_texts(seeded, ME))


def test_a_forget_that_cannot_finish_is_503_not_ok(seeded, monkeypatch):
    import provenance_answers as pa

    async def boom(*_a, **_k):
        raise RuntimeError("exact-words store down")

    monkeypatch.setattr(pa, "_erase_words", boom)
    rid = row_id(seeded, ME, "dark roast")
    r = as_member().post(f"/api/memories/mine/{rid}/forget", headers=H)
    assert r.status_code == 503, "the verbatim words could not be erased: never report 'forgotten'"


def test_a_retry_after_a_failed_twin_finishes_the_whole_job(seeded, monkeypatch):
    """The first forget rejects the row, then a twin's reject fails (503). The retry must redo the twins and the erase, not
    only the words - or the twin keeps the fact alive while the route says 'forgotten'."""
    import provenance_answers as pa

    rid = row_id(seeded, ME, "dark roast")
    twin = row_id(seeded, ME, "bike")                     # stands in for a twin row of the same utterance
    ref_twin = run(seeded.get(twin))

    async def twins(_uid, _ref, svc=None, **_kw):
        return [ref_twin] if _ref.id == rid else []

    monkeypatch.setattr(pa, "twins_of", twins)
    real_review = seeded.review
    calls = {"n": 0}

    async def flaky(mem_id, **kw):
        if mem_id == twin and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("store hiccup")
        return await real_review(mem_id, **kw)

    monkeypatch.setattr(seeded, "review", flaky)
    c = as_member()
    assert c.post(f"/api/memories/mine/{rid}/forget", headers=H).status_code == 503
    assert any("bike" in t for t in approved_texts(seeded, ME)), "the twin is still there after the failed first try"
    r = c.post(f"/api/memories/mine/{rid}/forget", headers=H)
    assert r.status_code == 200, r.text
    assert not any("bike" in t for t in approved_texts(seeded, ME)), "the retry takes the twin too"
    assert not any("dark roast" in t for t in approved_texts(seeded, ME))


def test_a_failed_physical_erase_is_503_and_the_retry_finishes(seeded, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "1")
    rid = row_id(seeded, ME, "dark roast")
    state = {"fail": True, "erased": []}

    async def erase(user_id, ids, **kw):          # the real one needs a live palace directory: stubbed here
        if state["fail"]:
            raise RuntimeError("disk busy")
        state["erased"].append(list(ids))
        return {"rows_removed": len(list(ids))}

    monkeypatch.setattr(seeded, "erase_rows", erase)
    c = as_member()
    assert c.post(f"/api/memories/mine/{rid}/forget", headers=H).status_code == 503, "text still on disk: never 'forgotten'"
    state["fail"] = False
    assert c.post(f"/api/memories/mine/{rid}/forget", headers=H).status_code == 200
    assert state["erased"] and rid in state["erased"][0], "the retry runs the physical erase for the already-rejected row"
    assert not any("dark roast" in t for t in approved_texts(seeded, ME))


def test_a_pending_row_is_not_forgettable_from_the_screen(seeded):
    pending = run(seeded.ingest("I secretly collect teapots from every place I visit.", user_id=ME, source="chat_regex",
                                confidence=0.4, session_id="s0", status="pending"))
    if pending is None:
        pytest.skip("the ingest gate dropped the pending row")
    r = as_member().post(f"/api/memories/mine/{pending.id}/forget", headers=H)
    assert r.status_code == 404


# ── negative controls: take the wall away and the leak is real ─────────────────────────────────

def test_control_without_the_identity_wall_a_guest_would_read_rows(seeded, monkeypatch):
    """Proves test_a_guest_never_gets_a_row can fail. The role matrix (require_feature_access) is a second wall behind
    require_member; with BOTH removed the same request returns the rows - so each assertion above is load-bearing."""
    from routers import memories

    async def allow(*_a, **_k):
        return None

    monkeypatch.setattr(memory_space, "require_member", lambda user, *, has_session_header: ME)
    monkeypatch.setattr(memories, "require_feature_access", allow)
    r = as_member(GUEST).get("/api/memories/mine", headers=H)
    assert r.status_code == 200 and "Marisol" in flat(r)


def test_control_the_identity_wall_alone_holds_without_the_role_matrix(seeded, monkeypatch):
    """The converse: with the role matrix removed, require_member still refuses a guest (it is not leaning on the matrix)."""
    from routers import memories

    async def allow(*_a, **_k):
        return None

    monkeypatch.setattr(memories, "require_feature_access", allow)
    r = as_member(GUEST).get("/api/memories/mine", headers=H)
    assert r.status_code == 403 and "Marisol" not in flat(r)


def test_control_the_ownership_check_is_what_answers_gone(seeded, monkeypatch):
    """With the ownership wall removed forget_row goes on to act on another member's row - and is stopped only by the memory
    service's own identity wall (a backstop, which refuses: nothing is forgotten). The 404 above is the ownership check."""
    import provenance_answers as pa

    other_id = row_id(seeded, OTHER, "Biscuit")
    assert run(memory_space.forget_row(ME, other_id, svc=seeded)) == "gone"
    monkeypatch.setattr(pa, "_owns", lambda meta, uid: True)
    with pytest.raises(RuntimeError):
        run(memory_space.forget_row(ME, other_id, svc=seeded))
    assert any("Biscuit" in t for t in approved_texts(seeded, OTHER)), "the service's own wall keeps the row"


def test_control_without_the_private_filter_health_words_would_ship(seeded, monkeypatch):
    import provenance_answers as pa

    monkeypatch.setattr(pa, "sensitive_classes", lambda text, meta=None: frozenset())
    r = as_member().get("/api/memories/mine", headers=H)
    assert "migraine" in flat(r).lower(), "the control must show the filter is what keeps the words off the wire"
