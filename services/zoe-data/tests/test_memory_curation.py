"""Caregiver curation (memories-space P7, ``memory_curation.py`` + ``routers/memories_curation.py``; flag ``ZOE_MEMORIES_CURATORS``
default off; owner decisions D7 / D9 / D10 / D11, 2026-10-19).

SYNTHETIC members and rows only (``demo_bar_`` ids, invented names), a fake Chroma and SQLite (``ci_safe``). Every wall has a
break-the-fix control here (``test_control_*`` or an inline ``off`` run):

  a child / a guest / a panel / a non-admin cannot curate   -> 403 / 401, nothing written
  a child cannot override a curated row                     -> the passing remark is HELD (disputed), the row stands
                                                               (control: ZOE_MEMORY_AUTHORITY=off lets it through)
  an adult is curated only in a category they opted in      -> 403 without; opting out releases the rows to their own word
  a sensitive category is refused without an opt-in        -> 403, and health words under another category -> 422
  provenance on every curated row                           -> curated_by / _name / _at / _via / _category, class curated
  forget is audited                                         -> review(reject) audit row + physical erase + curation audit
  nothing curated + sensitive reaches the panel             -> counted private (control: without the category stamp it shows)
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import db_compat
import memory_authority as ma
import memory_curation as mc
import memory_service
import memory_space
import provenance_answers as pa
from db_pool import _Cursor, _ExecResult
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
PARENT = "demo_bar_0000000a"
PARENT2 = "demo_bar_0000000b"
KID = "demo_bar_0000000c"
KID2 = "demo_bar_0000000d"
ADULT = "demo_bar_0000000e"
H = dict()
H["X-Session-ID"] = "sess-member"


def run(coro):
    return asyncio.run(coro)


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)

    def execute(self, sql, params=()):
        async def _run():
            await asyncio.sleep(0)
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()


def _migration():
    spec = importlib.util.spec_from_file_location("mig_0061", SVC / "alembic/versions/0061_memory_curation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_MEMORIES_CURATORS", "1")
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "1")
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-memory-curation")
    col = _Col()
    s._collection = lambda: col
    audit: list = []
    erased: list = []

    async def record_audit(**kw):
        audit.append(kw)

    async def not_opted_out(_uid):
        return False

    async def erase_rows(user_id, ids, *, actor, reason=""):
        erased.append((user_id, list(ids), actor))
        return dict(rows_removed=len(list(ids)))

    s._append_audit = record_audit
    s.erase_rows = erase_rows
    monkeypatch.setattr(memory_service, "_user_opted_out", not_opted_out)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    db = _Sqlite(str(tmp_path / "zoe.db"))
    db.conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, role TEXT, name TEXT)")
    db.conn.execute("CREATE TABLE auth_users (user_id TEXT PRIMARY KEY, role TEXT)")
    db.conn.execute("CREATE TABLE member_modes (user_id TEXT PRIMARY KEY, mode TEXT, minor INTEGER)")
    people = [(PARENT, "admin", "Sam Parker"), (PARENT2, "admin", "Alex Parker"), (KID, "child", "Mia Parker"),
              (KID2, "child", "Leo Parker"), (ADULT, "user", "Robin Parker")]
    # the data-plane mirror says member for everyone (as live): roles come from auth_users only
    db.conn.executemany("INSERT INTO users VALUES (?, ?, ?)", [(u, "member", n) for u, _r, n in people])
    db.conn.executemany("INSERT INTO auth_users VALUES (?, ?)", [(u, r) for u, r, _n in people])
    for sql in _migration().statements("sqlite"):
        db.conn.execute(sql)
    db.conn.commit()

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)

    async def no_names(*_a, **_k):
        return frozenset()

    monkeypatch.setattr(pa, "_own_names", no_names, raising=False)
    monkeypatch.setattr(pa, "account_names", no_names, raising=False)

    async def no_people(_uid):
        return []

    monkeypatch.setattr(pa, "load_people", no_people)
    caller = dict(user=None)
    from auth import get_current_user
    from routers.memories_curation import router

    app = FastAPI()
    app.include_router(router)

    async def _user(request: Request):
        return dict(caller["user"])

    app.dependency_overrides[get_current_user] = _user
    yield type("Env", (), dict(svc=s, col=col, audit=audit, erased=erased, db=db, caller=caller, client=TestClient(app)))


def as_(env, uid, role):
    env.caller["user"] = dict(user_id=uid, role=role)
    return env.client


def rows_of(env, uid, status="approved"):
    return run(env.svc.list_by_status(user_id=uid, status=status, limit=500))


def curate(env, text, category, member=KID, who=PARENT):
    return as_(env, who, "admin").post(f"/api/memories/curation/members/{member}/rows",
                                       json=dict(text=text, category=category), headers=H)


def curation_audit(env):
    return env.db.conn.execute("SELECT actor_user_id, member_user_id, action, category, row_ref, phase, ok "
                               "FROM memory_curation_audit ORDER BY at, rowid").fetchall()


# -- flag + who may curate ----------------------------------------------------------------------------

def test_flag_off_every_route_is_404(env, monkeypatch):
    c = as_(env, PARENT, "admin")
    assert c.get("/api/memories/curation/members", headers=H).status_code == 200      # control: on
    monkeypatch.delenv("ZOE_MEMORIES_CURATORS")
    assert c.get("/api/memories/curation/members", headers=H).status_code == 404
    assert curate(env, "Mia likes dinosaurs", "likes").status_code == 404
    assert c.get("/api/memories/curation/me", headers=H).status_code == 404
    assert not rows_of(env, KID)


def test_a_parent_adds_a_memory_for_a_child_with_provenance(env):
    r = curate(env, "Mia's teacher is Ms Lee", "school")
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "Mia's teacher is Ms Lee" and r.json()["added_by"] == "Sam Parker"
    (row,) = rows_of(env, KID)
    md = row.metadata
    assert row.text == "User's teacher is Ms Lee"                       # the store convention: the child is the user
    assert md["origin"] == "curator" and md["authority_class"] == ma.CURATED and md["authority_basis"] == "curator"
    assert md["curated_by"] == PARENT and md["curated_by_name"] == "Sam Parker" and md["curated_via"] == "desktop"
    assert md["curated_category"] == "school" and md["curated_sensitive"] is False and md["curated_at"]
    assert not any(k.startswith("candidate_curated") for k in md), "curated stamps are first-class"
    got = curation_audit(env)
    assert [(a[2], a[5]) for a in got] == [("add", "requested"), ("add", "finished")]
    assert got[1][6] == 1 and got[0][0] == PARENT and got[0][1] == KID
    assert not any("Lee" in str(v) for a in got for v in a), "the audit never holds memory text"
    assert not rows_of(env, PARENT), "nothing lands in the curator own store"


def test_every_adult_admin_curates_every_child_without_a_grant(env):
    assert curate(env, "Leo's swimming is on Tuesdays", "routines", member=KID2, who=PARENT2).status_code == 200
    names = [m["user_id"] for m in as_(env, PARENT, "admin").get("/api/memories/curation/members", headers=H).json()["members"]]
    assert KID in names and KID2 in names and PARENT2 not in names and ADULT not in names


def test_a_child_cannot_curate(env):
    c = as_(env, KID, "child")
    r = c.post(f"/api/memories/curation/members/{KID2}/rows", json=dict(text="Leo likes trucks", category="likes"), headers=H)
    assert r.status_code == 403
    assert c.get("/api/memories/curation/members", headers=H).status_code == 403
    # a child session that CLAIMS admin is still a child: the role comes from the account too
    r = as_(env, KID, "admin").post(f"/api/memories/curation/members/{KID2}/rows",
                                    json=dict(text="Leo likes trucks", category="likes"), headers=H)
    assert r.status_code == 403
    assert not rows_of(env, KID2)


def test_a_guest_a_panel_and_a_non_admin_cannot_curate(env):
    body = dict(text="Mia likes dinosaurs", category="likes")
    url = f"/api/memories/curation/members/{KID}/rows"
    assert as_(env, "guest", "guest").post(url, json=body, headers=H).status_code in (401, 403)
    assert as_(env, PARENT, "admin").post(url, json=body).status_code == 401              # no session header
    panel = dict(H)
    panel["X-Device-Token"] = "panel-token"
    assert as_(env, PARENT, "admin").post(url, json=body, headers=panel).status_code == 403
    c = as_(env, PARENT, "admin")
    c.cookies.set("zoe_panel_kiosk", "kiosk")
    assert c.post(url, json=body, headers=H).status_code == 403
    c.cookies.clear()
    assert as_(env, ADULT, "user").post(url, json=body, headers=H).status_code == 403      # an adult, not an admin
    env.db.conn.execute("UPDATE auth_users SET role = ? WHERE user_id = ?", ("user", PARENT2))
    env.db.conn.commit()
    assert as_(env, PARENT2, "admin").post(url, json=body, headers=H).status_code == 403    # the session claim alone is not enough
    assert not rows_of(env, KID)
    assert curate(env, "Mia likes dinosaurs", "likes").status_code == 200                    # control: a real curator


def test_curating_yourself_or_a_stranger_is_refused(env):
    assert curate(env, "I like tea", "likes", member=PARENT).status_code == 400
    assert curate(env, "Someone likes tea", "likes", member="demo_bar_000000ff").status_code == 404


# -- adults: their own word wins unless they opted the category in (D9) -------------------------------

def test_a_curator_cannot_curate_an_adult_without_their_own_opt_in(env):
    assert curate(env, "Robin's bins go out on Monday night", "routines", member=ADULT).status_code == 403
    assert not rows_of(env, ADULT)
    # an admin cannot opt an adult in on their behalf (D8 is a lawyer question, not a setting)
    r = as_(env, PARENT, "admin").put(f"/api/memories/curation/members/{ADULT}/sensitive/health", json=dict(on=True), headers=H)
    assert r.status_code == 403
    # the adult opts ONE category in, themself
    assert as_(env, ADULT, "user").put("/api/memories/curation/me/routines", json=dict(on=True), headers=H).json()["on"] is True
    assert curate(env, "Robin's bins go out on Monday night", "routines", member=ADULT).status_code == 200
    assert curate(env, "Robin's teacher is Ms Lee", "school", member=ADULT).status_code == 403     # not opted in
    (row,) = rows_of(env, ADULT)
    assert row.metadata["authority_class"] == ma.CURATED
    assert run(memory_space.forget_row(ADULT, row.id)) == "protected"     # opted in: the curated note stands
    # opting out releases it: the adult own word wins again, and the curator no longer sees it
    out = as_(env, ADULT, "user").put("/api/memories/curation/me/routines", json=dict(on=False), headers=H).json()
    assert out["rows"] == 1
    (row,) = rows_of(env, ADULT)
    assert row.metadata["authority_class"] == ma.USER_STATED and row.metadata["authority_basis"] == "curated_released"
    assert row.metadata["origin"] == "curator"                            # provenance kept
    assert curate(env, "Robin's bins go out on Tuesday night", "routines", member=ADULT).status_code == 403
    assert run(memory_space.forget_row(ADULT, row.id)) == "forgotten"


def test_a_child_cannot_change_their_own_opt_ins(env):
    r = as_(env, KID, "child").put("/api/memories/curation/me/health", json=dict(on=True), headers=H)
    assert r.status_code == 403
    assert as_(env, KID, "child").get("/api/memories/curation/me", headers=H).json()["editable"] is False


# -- sensitive categories (D10) -----------------------------------------------------------------------

def test_sensitive_curation_is_refused_without_an_opt_in(env):
    r = curate(env, "Mia is allergic to peanuts", "health")
    assert r.status_code == 403 and "Health notes for Mia are off" in r.json()["detail"]
    r = curate(env, "Mia is allergic to peanuts", "routines")                     # health words under another category
    assert r.status_code == 422 and "health" in r.json()["detail"]
    assert curate(env, "Mia gets 5 dollars pocket money each week", "likes").status_code == 422
    assert not rows_of(env, KID)
    on = as_(env, PARENT, "admin").put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=True), headers=H)
    assert on.status_code == 200 and on.json()["changed"] is True
    r = curate(env, "Mia is allergic to peanuts", "health")
    assert r.status_code == 200 and r.json()["sensitive"] is True
    assert curate(env, "Mia gets 5 dollars pocket money each week", "money").status_code == 403    # money is still off
    assert [a[2] for a in curation_audit(env) if a[5] == "requested"] == ["sensitive_on", "add"]


def test_a_sensitive_category_switched_off_again_hides_its_notes(env):
    c = as_(env, PARENT, "admin")
    c.put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=True), headers=H)
    assert curate(env, "Mia is allergic to peanuts", "health").status_code == 200
    rid = rows_of(env, KID)[0].id
    c = as_(env, PARENT, "admin")
    c.put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=False), headers=H)
    got = c.get(f"/api/memories/curation/members/{KID}/rows", headers=H).json()
    assert got["rows"] == [] and got["hidden"] == 1 and "peanut" not in str(got)
    assert c.post(f"/api/memories/curation/members/{KID}/rows/{rid}/edit", json=dict(text="Mia is allergic to cats"),
                  headers=H).status_code == 404
    assert env.col.rows[rid][1]["status"] == "approved"                  # still the child note, still protected
    c.put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=True), headers=H)
    assert len(c.get(f"/api/memories/curation/members/{KID}/rows", headers=H).json()["rows"]) == 1   # control: back on


def test_feelings_and_family_conflict_are_never_curated(env):
    r = curate(env, "Mia had a huge fight with her brother and they are not speaking", "people")
    assert r.status_code == 422
    assert not rows_of(env, KID)


# -- the child cannot override a curated row (D9) -------------------------------------------------------

def _curated_teacher(env):
    assert curate(env, "Mia's teacher is Ms Lee", "school").status_code == 200
    return rows_of(env, KID)[0].id


def test_a_child_passing_remark_is_held_not_written_over_the_curated_row(env):
    rid = _curated_teacher(env)
    ref = run(env.svc.ingest("User's teacher is Mr Brown.", user_id=KID, source="chat_regex", status="approved"))
    assert ref.metadata["status"] == "disputed" and ref.metadata["contradicts_id"] == rid
    assert env.col.rows[rid][1]["status"] == "approved"
    # nor through the review UI, nor a forget / correct from the Memories screen, nor approving the held note
    assert run(env.svc.review(rid, decision="reject", actor=KID)) is None
    assert run(env.svc.review(rid, decision="edit", edits="User's teacher is Mr Brown.", actor=KID)) is None
    assert run(env.svc.review(ref.id, decision="approve", actor=KID)) is None
    assert env.col.rows[ref.id][1]["status"] == "disputed"
    assert run(memory_space.forget_row(KID, rid)) == "protected"
    assert run(memory_space.correct_row(KID, rid, "My teacher is Mr Brown")) == "protected"
    assert env.col.rows[rid][1]["status"] == "approved"


def test_control_authority_off_lets_the_child_remark_through(env, monkeypatch):
    _curated_teacher(env)
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "off")
    ref = run(env.svc.ingest("User's teacher is Mr Brown.", user_id=KID, source="chat_regex", status="approved"))
    assert ref.metadata["status"] == "approved", "without the wall the remark stands beside the curated row"


def test_a_passing_denial_is_held_too(env):
    as_(env, PARENT, "admin").put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=True), headers=H)
    assert curate(env, "Mia is allergic to peanuts", "health").status_code == 200
    ref = run(env.svc.ingest("User is not allergic to peanuts.", user_id=KID, source="chat_regex", status="approved"))
    assert ref.metadata["status"] == "disputed"
    assert ma.negation_flip("User is not allergic to peanuts.", "User is allergic to peanuts")
    assert not ma.negation_flip("User is allergic to cats.", "User is allergic to peanuts")


def test_the_curator_keeps_or_accepts_a_held_note(env):
    rid = _curated_teacher(env)
    run(env.svc.ingest("User's teacher is Mr Brown.", user_id=KID, source="chat_regex", status="approved"))
    c = as_(env, PARENT2, "admin")                                      # any adult admin, not only the one who added it
    held = c.get(f"/api/memories/curation/members/{KID}/held", headers=H).json()["held"]
    assert len(held) == 1 and held[0]["text"] == "Mia's teacher is Mr Brown." and held[0]["against"]["id"] == rid
    assert c.get(f"/api/memories/curation/members/{KID}/rows", headers=H).json()["held"] == 1
    r = c.post(f"/api/memories/curation/members/{KID}/held/{held[0]['id']}", json=dict(action="keep"), headers=H)
    assert r.status_code == 200
    assert env.col.rows[held[0]["id"]][1]["status"] == "rejected" and env.col.rows[rid][1]["status"] == "approved"
    run(env.svc.ingest("User's teacher is Mr Green.", user_id=KID, source="chat_regex", status="approved"))
    (note,) = c.get(f"/api/memories/curation/members/{KID}/held", headers=H).json()["held"]
    r = c.post(f"/api/memories/curation/members/{KID}/held/{note['id']}", json=dict(action="accept"), headers=H)
    assert r.status_code == 200
    texts = [x.text for x in rows_of(env, KID)]
    assert texts == ["User's teacher is Mr Green."]
    new = rows_of(env, KID)[0].metadata
    assert new["authority_class"] == ma.CURATED and new["curated_by"] == PARENT2 and new["supersedes_id"] == rid
    assert env.col.rows[rid][1]["status"] == "superseded"


def test_a_curator_sees_only_curated_rows_never_the_child_other_memories(env):
    run(env.svc.ingest("User likes dinosaurs and drawing.", user_id=KID, source="chat_regex", status="approved"))
    _curated_teacher(env)
    got = as_(env, PARENT, "admin").get(f"/api/memories/curation/members/{KID}/rows", headers=H).json()
    assert [r["text"] for r in got["rows"]] == ["Mia's teacher is Ms Lee"]
    assert "dinosaurs" not in str(got)


# -- forget is audited ----------------------------------------------------------------------------------

def test_a_curator_forget_goes_through_the_audited_path(env):
    rid = _curated_teacher(env)
    r = as_(env, PARENT, "admin").post(f"/api/memories/curation/members/{KID}/rows/{rid}/forget", headers=H)
    assert r.status_code == 200
    assert env.col.rows[rid][1]["status"] == "rejected"
    assert [(a["action"], a["mem_id"], a["actor"]) for a in env.audit if a["action"] == "reject"] == [("reject", rid, PARENT)]
    assert env.erased == [(KID, [rid], PARENT)], "forgotten means forever: the row is erased for real"
    assert [(a[2], a[5], a[6]) for a in curation_audit(env)][-2:] == [("forget", "requested", None), ("forget", "finished", 1)]
    assert not as_(env, PARENT, "admin").get(f"/api/memories/curation/members/{KID}/rows", headers=H).json()["rows"]


def test_no_audit_no_change(env):
    rid = _curated_teacher(env)
    env.db.conn.execute("DROP TABLE memory_curation_audit")
    env.db.conn.commit()
    r = as_(env, PARENT, "admin").post(f"/api/memories/curation/members/{KID}/rows/{rid}/forget", headers=H)
    assert r.status_code == 503
    assert env.col.rows[rid][1]["status"] == "approved" and not env.erased


def test_a_held_decision_is_audited_before_anything_changes(env, monkeypatch):
    rid = _curated_teacher(env)
    run(env.svc.ingest("User's teacher is Mr Brown.", user_id=KID, source="chat_regex", status="approved"))
    c = as_(env, PARENT, "admin")
    (note,) = c.get(f"/api/memories/curation/members/{KID}/held", headers=H).json()["held"]
    real = mc.audit

    async def refuse_held(db, **kw):                # the audit is down for the held decision only (the edit audit works)
        if kw.get("action", "").startswith("held"):
            raise RuntimeError("audit down")
        return await real(db, **kw)

    monkeypatch.setattr(mc, "audit", refuse_held)
    r = c.post(f"/api/memories/curation/members/{KID}/held/{note['id']}", json=dict(action="accept"), headers=H)
    assert r.status_code == 503
    assert env.col.rows[rid][1]["status"] == "approved" and env.col.rows[note["id"]][1]["status"] == "disputed"


def test_a_curator_cannot_forget_the_child_other_memories(env):
    own = run(env.svc.ingest("User likes dinosaurs and drawing.", user_id=KID, source="chat_regex", status="approved"))
    r = as_(env, PARENT, "admin").post(f"/api/memories/curation/members/{KID}/rows/{own.id}/forget", headers=H)
    assert r.status_code == 404
    assert env.col.rows[own.id][1]["status"] == "approved"


# -- never on the wall (D11) ----------------------------------------------------------------------------

def test_a_curated_sensitive_row_never_reaches_the_panel(env):
    as_(env, PARENT, "admin").put(f"/api/memories/curation/members/{KID}/sensitive/medication", json=dict(on=True), headers=H)
    # words the classifiers do not see as health: only the curated category keeps it off the wall
    assert curate(env, "Mia takes ventolin before sport", "medication").status_code == 200
    assert curate(env, "Mia's swimming is on Tuesdays", "routines").status_code == 200
    view = run(memory_space.read_view(KID, svc=env.svc))
    shown = str(view["groups"])
    assert "ventolin" not in shown and view["private"].get("health") == 1
    assert "swimming" in shown and "Added for you by Sam Parker" in shown
    # the curation routes refuse a panel outright, so the curator list cannot be read on a wall either
    panel = dict(H)
    panel["X-Device-Token"] = "panel-token"
    assert as_(env, PARENT, "admin").get(f"/api/memories/curation/members/{KID}/rows", headers=panel).status_code == 403


def test_control_without_the_category_stamp_the_row_would_show(env):
    as_(env, PARENT, "admin").put(f"/api/memories/curation/members/{KID}/sensitive/medication", json=dict(on=True), headers=H)
    assert curate(env, "Mia takes ventolin before sport", "medication").status_code == 200
    rid = rows_of(env, KID)[0].id
    doc, md = env.col.rows[rid]
    env.col.rows[rid] = (doc, dict((k, v) for k, v in md.items() if k != "curated_category"))
    assert "ventolin" in str(run(memory_space.read_view(KID, svc=env.svc))["groups"])


# -- static walls + migration ---------------------------------------------------------------------------

def _writes_as_curator(src: str) -> bool:
    """Passes the curator writer as a source / origin: the literal, a local constant holding it, or memory_space.CURATED_ORIGIN."""
    names = ["\x22curator\x22", "CURATED_ORIGIN"] + re.findall(r"^(\w+)\s*=\s*\x22curator\x22\s*$", src, re.M)
    if "memory_curation" in src:
        names.append("WRITER")                    # memory_curation.WRITER / mc.WRITER through an import
    return any(re.search(r"(?<![\w\x60])(?:source|origin)\s*=\s*(?:\w+\.)?" + re.escape(n) + r"(?!\w)", src) for n in names)


def test_only_memory_curation_writes_as_the_curator():
    hits = sorted(p.name for p in SVC.rglob("*.py") if "tests" not in p.parts and _writes_as_curator(p.read_text(errors="ignore")))
    assert hits == ["memory_curation.py"], hits
    assert _writes_as_curator("ref = svc.ingest(t, origin=\x22curator\x22)")          # control: the literal is caught
    assert not _writes_as_curator("WRITER = \x22ask\x22\nsvc.ingest(t, source=WRITER)")
    assert _writes_as_curator("import memory_curation as mc\nsvc.ingest(t, origin=mc.WRITER)")   # control: an alias is caught


def test_migration_runs_twice_on_sqlite_and_chains_onto_one_head():
    mod = _migration()
    con = sqlite3.connect(":memory:")
    for _ in range(2):
        for sql in mod.statements("sqlite"):
            con.execute(sql)
    cols = [r[1] for r in con.execute("PRAGMA table_info(memory_curation_audit)")]
    assert "text" not in cols and "content" not in cols, "the audit table has no column for memory text"
    revs, downs = set(), set()
    for f in (SVC / "alembic/versions").glob("*.py"):
        src = f.read_text()
        r = re.search(r"^revision = \x22(\w+)\x22", src, re.M)
        d = re.search(r"^down_revision = \x22(\w+)\x22", src, re.M)
        if r:
            revs.add(r.group(1))
        if d:
            downs.add(d.group(1))
    assert mod.revision == "0061" and mod.down_revision in revs
    assert len(revs - downs) == 1
