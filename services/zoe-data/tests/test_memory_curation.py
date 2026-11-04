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
    spec = importlib.util.spec_from_file_location("mig_0066", SVC / "alembic/versions/0066_memory_curation.py")
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
    people = [(PARENT, "admin", "Ash Quill"), (PARENT2, "admin", "Tam Quill"), (KID, "child", "Juno Quill"),
              (KID2, "child", "Rafe Quill"), (ADULT, "user", "Wren Quill")]
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
    caller = dict(user=None, auth_calls=0)
    from auth import get_current_user
    from routers.memories_curation import router

    app = FastAPI()
    app.include_router(router)

    async def _user(request: Request):
        caller["auth_calls"] += 1
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
    assert curate(env, "Juno likes dinosaurs", "likes").status_code == 404
    assert c.get("/api/memories/curation/me", headers=H).status_code == 404
    assert not rows_of(env, KID)


def test_a_parent_adds_a_memory_for_a_child_with_provenance(env):
    r = curate(env, "Juno's teacher is Ms Lee", "school")
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "Juno's teacher is Ms Lee" and r.json()["added_by"] == "Ash Quill"
    (row,) = rows_of(env, KID)
    md = row.metadata
    assert row.text == "User's teacher is Ms Lee"                       # the store convention: the child is the user
    assert md["origin"] == "curator" and md["authority_class"] == ma.CURATED and md["authority_basis"] == "curator"
    # D1 / D7 (person-centred memory, 2026-11-03): the guardian's own statement - written at once, provenance user_stated
    assert md["status"] == "approved" and md["authority"] == "user_stated"
    assert md["curated_by"] == PARENT and md["curated_by_name"] == "Ash Quill" and md["curated_via"] == "desktop"
    assert md["curated_category"] == "school" and md["curated_sensitive"] is False and md["curated_at"]
    assert not any(k.startswith("candidate_curated") for k in md), "curated stamps are first-class"
    got = curation_audit(env)
    assert [(a[2], a[5]) for a in got] == [("add", "requested"), ("add", "finished")]
    assert got[1][6] == 1 and got[0][0] == PARENT and got[0][1] == KID
    assert not any("Lee" in str(v) for a in got for v in a), "the audit never holds memory text"
    assert not rows_of(env, PARENT), "nothing lands in the curator own store"


def test_every_adult_admin_curates_every_child_without_a_grant(env):
    assert curate(env, "Rafe's swimming is on Tuesdays", "routines", member=KID2, who=PARENT2).status_code == 200
    names = [m["user_id"] for m in as_(env, PARENT, "admin").get("/api/memories/curation/members", headers=H).json()["members"]]
    assert KID in names and KID2 in names and PARENT2 not in names and ADULT not in names


def test_a_child_cannot_curate(env):
    c = as_(env, KID, "child")
    r = c.post(f"/api/memories/curation/members/{KID2}/rows", json=dict(text="Rafe likes trucks", category="likes"), headers=H)
    assert r.status_code == 403
    assert c.get("/api/memories/curation/members", headers=H).status_code == 403
    # a child session that CLAIMS admin is still a child: the role comes from the account too
    r = as_(env, KID, "admin").post(f"/api/memories/curation/members/{KID2}/rows",
                                    json=dict(text="Rafe likes trucks", category="likes"), headers=H)
    assert r.status_code == 403
    assert not rows_of(env, KID2)


def test_a_guest_a_panel_and_a_non_admin_cannot_curate(env):
    body = dict(text="Juno likes dinosaurs", category="likes")
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
    assert curate(env, "Juno likes dinosaurs", "likes").status_code == 200                    # control: a real curator


def test_curating_yourself_or_a_stranger_is_refused(env):
    assert curate(env, "I like tea", "likes", member=PARENT).status_code == 400
    assert curate(env, "Someone likes tea", "likes", member="demo_bar_000000ff").status_code == 404


# -- adults: their own word wins unless they opted the category in (D9) -------------------------------

def test_a_curator_cannot_curate_an_adult_without_their_own_opt_in(env):
    assert curate(env, "Wren's bins go out on Monday night", "routines", member=ADULT).status_code == 403
    assert not rows_of(env, ADULT)
    # an admin cannot opt an adult in on their behalf (D8 is a lawyer question, not a setting)
    r = as_(env, PARENT, "admin").put(f"/api/memories/curation/members/{ADULT}/sensitive/health", json=dict(on=True), headers=H)
    assert r.status_code == 403
    # the adult opts ONE category in, themself
    assert as_(env, ADULT, "user").put("/api/memories/curation/me/routines", json=dict(on=True), headers=H).json()["on"] is True
    assert curate(env, "Wren's bins go out on Monday night", "routines", member=ADULT).status_code == 200
    assert curate(env, "Wren's teacher is Ms Lee", "school", member=ADULT).status_code == 403     # not opted in
    (row,) = rows_of(env, ADULT)
    assert row.metadata["authority_class"] == ma.CURATED
    assert run(memory_space.forget_row(ADULT, row.id)) == "protected"     # opted in: the curated note stands
    # opting out releases it: the adult own word wins again, and the curator no longer sees it
    out = as_(env, ADULT, "user").put("/api/memories/curation/me/routines", json=dict(on=False), headers=H).json()
    assert out["rows"] == 1
    (row,) = rows_of(env, ADULT)
    assert row.metadata["authority_class"] == ma.USER_STATED and row.metadata["authority_basis"] == "curated_released"
    assert row.metadata["origin"] == "curator"                            # provenance kept
    assert curate(env, "Wren's bins go out on Tuesday night", "routines", member=ADULT).status_code == 403
    assert run(memory_space.forget_row(ADULT, row.id)) == "forgotten"


def test_a_child_cannot_change_their_own_opt_ins(env):
    r = as_(env, KID, "child").put("/api/memories/curation/me/health", json=dict(on=True), headers=H)
    assert r.status_code == 403
    assert as_(env, KID, "child").get("/api/memories/curation/me", headers=H).json()["editable"] is False


# -- sensitive categories (D10) -----------------------------------------------------------------------

def test_sensitive_curation_is_refused_without_an_opt_in(env):
    r = curate(env, "Juno is allergic to peanuts", "health")
    assert r.status_code == 403 and "Health notes for Juno are off" in r.json()["detail"]
    r = curate(env, "Juno is allergic to peanuts", "routines")                     # health words under another category
    assert r.status_code == 422 and "health" in r.json()["detail"]
    assert curate(env, "Juno gets 5 dollars pocket money each week", "likes").status_code == 422
    assert not rows_of(env, KID)
    on = as_(env, PARENT, "admin").put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=True), headers=H)
    assert on.status_code == 200 and on.json()["changed"] is True
    r = curate(env, "Juno is allergic to peanuts", "health")
    assert r.status_code == 200 and r.json()["sensitive"] is True
    assert curate(env, "Juno gets 5 dollars pocket money each week", "money").status_code == 403    # money is still off
    assert [a[2] for a in curation_audit(env) if a[5] == "requested"] == ["sensitive_on", "add"]


def test_a_sensitive_category_switched_off_again_hides_its_notes(env):
    c = as_(env, PARENT, "admin")
    c.put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=True), headers=H)
    assert curate(env, "Juno is allergic to peanuts", "health").status_code == 200
    rid = rows_of(env, KID)[0].id
    c = as_(env, PARENT, "admin")
    c.put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=False), headers=H)
    got = c.get(f"/api/memories/curation/members/{KID}/rows", headers=H).json()
    assert got["rows"] == [] and got["hidden"] == 1 and "peanut" not in str(got)
    assert c.post(f"/api/memories/curation/members/{KID}/rows/{rid}/edit", json=dict(text="Juno is allergic to cats"),
                  headers=H).status_code == 404
    assert env.col.rows[rid][1]["status"] == "approved"                  # still the child note, still protected
    c.put(f"/api/memories/curation/members/{KID}/sensitive/health", json=dict(on=True), headers=H)
    assert len(c.get(f"/api/memories/curation/members/{KID}/rows", headers=H).json()["rows"]) == 1   # control: back on


def test_feelings_and_family_conflict_are_never_curated(env):
    r = curate(env, "Juno had a huge fight with her brother and they are not speaking", "people")
    assert r.status_code == 422
    assert not rows_of(env, KID)


# -- the child cannot override a curated row (D9) -------------------------------------------------------

def _curated_teacher(env):
    assert curate(env, "Juno's teacher is Ms Lee", "school").status_code == 200
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
    assert curate(env, "Juno is allergic to peanuts", "health").status_code == 200
    ref = run(env.svc.ingest("User is not allergic to peanuts.", user_id=KID, source="chat_regex", status="approved"))
    assert ref.metadata["status"] == "disputed"
    assert ma.negation_flip("User is not allergic to peanuts.", "User is allergic to peanuts")
    assert not ma.negation_flip("User is allergic to cats.", "User is allergic to peanuts")


def test_the_curator_keeps_or_accepts_a_held_note(env):
    rid = _curated_teacher(env)
    run(env.svc.ingest("User's teacher is Mr Brown.", user_id=KID, source="chat_regex", status="approved"))
    c = as_(env, PARENT2, "admin")                                      # any adult admin, not only the one who added it
    held = c.get(f"/api/memories/curation/members/{KID}/held", headers=H).json()["held"]
    assert len(held) == 1 and held[0]["text"] == "Juno's teacher is Mr Brown." and held[0]["against"]["id"] == rid
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
    assert [r["text"] for r in got["rows"]] == ["Juno's teacher is Ms Lee"]
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


def test_a_forget_whose_erase_failed_is_retryable(env):
    rid = _curated_teacher(env)
    real = env.svc.erase_rows

    async def boom(*a, **k):
        raise memory_service.MemoryServiceError("disk")

    env.svc.erase_rows = boom
    url = f"/api/memories/curation/members/{KID}/rows/{rid}/forget"
    assert as_(env, PARENT, "admin").post(url, headers=H).status_code == 503
    assert env.col.rows[rid][1]["status"] == "rejected" and not env.erased        # rejected, text still on disk
    env.svc.erase_rows = real
    assert as_(env, PARENT, "admin").post(url, headers=H).status_code == 200      # the retry finishes the erase
    assert env.erased == [(KID, [rid], PARENT)]
    # control: a row that was never a curated row of this member is still a 404
    other = run(env.svc.ingest("User likes dinosaurs.", user_id=KID, source="chat_regex", status="approved"))
    assert as_(env, PARENT, "admin").post(f"/api/memories/curation/members/{KID}/rows/{other.id}/forget", headers=H).status_code == 404


def test_a_curated_note_older_than_a_page_of_newer_rows_is_still_reachable(env):
    """Codex #2158: the page limit was applied BEFORE the curated filter, so 1,000 newer approved rows hid an older curated note
    (edit / forget 404, the child could not remove it either)."""
    rid = _curated_teacher(env)
    doc, md = env.col.rows[rid]
    for n in range(1001):                                    # newer, ordinary approved rows of the same child
        other = dict(md, origin="chat_regex", source="chat_regex", added_at="2099-01-01T00:00:%02d.%06dZ" % (n % 60, n))
        other.pop("authority_class", None)
        env.col.rows["filler-%04d" % n] = ("User likes thing number %d." % n, other)
    # control: a page of 1,000 approved rows does not reach the curated note
    assert rid not in [r.id for r in run(env.svc.list_by_status(user_id=KID, status="approved", limit=1000))]
    c = as_(env, PARENT, "admin")
    assert [r["id"] for r in c.get(f"/api/memories/curation/members/{KID}/rows", headers=H).json()["rows"]] == [rid]
    assert c.post(f"/api/memories/curation/members/{KID}/rows/{rid}/forget", headers=H).status_code == 200
    assert env.col.rows[rid][1]["status"] == "rejected"


def test_forgetting_an_edited_note_erases_its_earlier_revisions(env):
    """Codex #2158: an edit keeps the old wording as a superseded row; forgetting the note must erase that too."""
    rid = _curated_teacher(env)
    c = as_(env, PARENT, "admin")
    r = c.post(f"/api/memories/curation/members/{KID}/rows/{rid}/edit", json=dict(text="Juno's teacher is Ms Park"), headers=H)
    assert r.status_code == 200
    new_id = r.json()["id"]
    assert new_id != rid and env.col.rows[rid][1]["status"] == "superseded"
    r = c.post(f"/api/memories/curation/members/{KID}/rows/{new_id}/edit", json=dict(text="Juno's teacher is Ms Holt"), headers=H)
    newest = r.json()["id"]
    # an unrelated superseded row of the child is NOT part of the chain
    own = run(env.svc.ingest("User likes dinosaurs.", user_id=KID, source="chat_regex", status="approved"))
    env.col.rows[own.id][1]["status"] = "superseded"
    assert c.post(f"/api/memories/curation/members/{KID}/rows/{newest}/forget", headers=H).status_code == 200
    ((uid, ids, actor),) = env.erased
    assert uid == KID and actor == PARENT and ids[0] == newest
    assert sorted(ids) == sorted([newest, new_id, rid]), "every earlier curated revision is erased with the note"


def test_editing_a_released_sensitive_note_keeps_it_private(env):
    """Codex #2158: a released medication note edited by its adult lost curated_category, so the new wording went public."""
    assert as_(env, ADULT, "user").put("/api/memories/curation/me/medication", json=dict(on=True), headers=H).json()["on"] is True
    assert curate(env, "Wren takes ventolin before sport", "medication", member=ADULT).status_code == 200
    assert as_(env, ADULT, "user").put("/api/memories/curation/me/medication", json=dict(on=False), headers=H).json()["rows"] == 1
    (row,) = rows_of(env, ADULT)
    edited = run(env.svc.review(row.id, decision="edit", edits=row.text.replace("before", "after"), actor=ADULT))
    assert edited is not None and "after" in edited.text
    md = env.col.rows[edited.id][1]
    assert md.get("curated_category") == "medication", "the privacy category rides the edit"
    assert not md.get("curated_by") and not md.get("curated_by_name"), "the attribution does not: the adult wrote this one"
    assert "health" in pa.sensitive_classes(edited.text, md)
    view = run(memory_space.read_view(ADULT, svc=env.svc))
    assert "ventolin" not in str(view["groups"]) and view["private"].get("health") == 1
    # control: without the carried category the edited wording would be public
    assert "health" not in pa.sensitive_classes(edited.text, dict((k, v) for k, v in md.items() if k != "curated_category"))


def test_an_accept_that_failed_to_close_the_note_stays_visible_and_retryable(env):
    rid = _curated_teacher(env)
    run(env.svc.ingest("User's teacher is Mr Brown.", user_id=KID, source="chat_regex", status="approved"))
    c = as_(env, PARENT, "admin")
    (note,) = c.get(f"/api/memories/curation/members/{KID}/held", headers=H).json()["held"]
    real = env.svc.review
    calls = dict(n=0)

    async def flaky(mem_id, **kw):
        if kw.get("decision") == "reject":
            calls["n"] += 1
            if calls["n"] == 1:
                return None                               # the close fails once, after the edit went through
        return await real(mem_id, **kw)

    env.svc.review = flaky
    url = f"/api/memories/curation/members/{KID}/held/{note['id']}"
    assert c.post(url, json=dict(action="accept"), headers=H).status_code == 409
    assert env.col.rows[rid][1]["status"] == "superseded"
    (again,) = c.get(f"/api/memories/curation/members/{KID}/held", headers=H).json()["held"]   # not stranded
    assert again["id"] == note["id"]
    for i in range(7):                                    # more supersedes than any hop cap (each failed retry adds one)
        (cur,) = c.get(f"/api/memories/curation/members/{KID}/rows", headers=H).json()["rows"]
        assert c.post(f"/api/memories/curation/members/{KID}/rows/{cur['id']}/edit", json=dict(text=f"Juno's teacher is Ms {chr(65 + i)}"),
                      headers=H).status_code == 200
    (again,) = c.get(f"/api/memories/curation/members/{KID}/held", headers=H).json()["held"]
    assert again["id"] == note["id"]
    calls["n"] = 99                                       # the close works again
    assert c.post(url, json=dict(action="keep"), headers=H).status_code == 200
    assert not c.get(f"/api/memories/curation/members/{KID}/held", headers=H).json()["held"]


def test_an_opt_out_that_cannot_list_rows_changes_nothing(env, monkeypatch):
    a = as_(env, ADULT, "user")
    assert a.put("/api/memories/curation/me/routines", json=dict(on=True), headers=H).status_code == 200
    assert curate(env, "Wren's bins go out on Monday night", "routines", member=ADULT).status_code == 200

    async def down(**kw):
        raise memory_service.MemoryServiceError("store down")

    monkeypatch.setattr(env.svc, "list_all_by_status_strict", down)
    a = as_(env, ADULT, "user")                                             # curate() switched the caller
    assert a.put("/api/memories/curation/me/routines", json=dict(on=False), headers=H).status_code == 503
    assert a.get("/api/memories/curation/me", headers=H).json()["on"] == ["routines"]      # still on, rows still protected
    (row,) = rows_of(env, ADULT)
    assert row.metadata["authority_class"] == ma.CURATED


def test_flag_off_is_404_before_authentication(env, monkeypatch):
    monkeypatch.delenv("ZOE_MEMORIES_CURATORS")
    env.caller["user"] = dict(user_id="guest", role="guest")
    assert env.client.get("/api/memories/curation/me").status_code == 404           # no session header, a guest: still 404
    assert env.caller["auth_calls"] == 0, "the flag answers before authentication ever runs"
    monkeypatch.setenv("ZOE_MEMORIES_CURATORS", "1")
    assert env.client.get("/api/memories/curation/me").status_code in (401, 403)    # control: on, the wall answers


def test_a_curator_cannot_forget_the_child_other_memories(env):
    own = run(env.svc.ingest("User likes dinosaurs and drawing.", user_id=KID, source="chat_regex", status="approved"))
    r = as_(env, PARENT, "admin").post(f"/api/memories/curation/members/{KID}/rows/{own.id}/forget", headers=H)
    assert r.status_code == 404
    assert env.col.rows[own.id][1]["status"] == "approved"


# -- never on the wall (D11) ----------------------------------------------------------------------------

def test_a_curated_sensitive_row_never_reaches_the_panel(env):
    as_(env, PARENT, "admin").put(f"/api/memories/curation/members/{KID}/sensitive/medication", json=dict(on=True), headers=H)
    # words the classifiers do not see as health: only the curated category keeps it off the wall
    assert curate(env, "Juno takes ventolin before sport", "medication").status_code == 200
    assert curate(env, "Juno's swimming is on Tuesdays", "routines").status_code == 200
    view = run(memory_space.read_view(KID, svc=env.svc))
    shown = str(view["groups"])
    assert "ventolin" not in shown and view["private"].get("health") == 1
    assert "swimming" in shown and "Added for you by Ash Quill" in shown
    # the curation routes refuse a panel outright, so the curator list cannot be read on a wall either
    panel = dict(H)
    panel["X-Device-Token"] = "panel-token"
    assert as_(env, PARENT, "admin").get(f"/api/memories/curation/members/{KID}/rows", headers=panel).status_code == 403


def test_control_without_the_category_stamp_the_row_would_show(env):
    as_(env, PARENT, "admin").put(f"/api/memories/curation/members/{KID}/sensitive/medication", json=dict(on=True), headers=H)
    assert curate(env, "Juno takes ventolin before sport", "medication").status_code == 200
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
    assert mod.revision == "0066" and mod.down_revision == "0065" and mod.down_revision in revs
    assert len(revs - downs) == 1


# -- person-centred memory D1 / D7 / forgotten-means-forever (accepted 2026-11-03) ------------------------------------------------

@pytest.fixture
def ledger(env, monkeypatch):
    import memory_forgotten as mf
    import memory_tombstones

    monkeypatch.setenv(mf.SALT_ENV, "curation-test-salt-0123456789")
    monkeypatch.delenv(mf.SHIELD_DAYS_ENV, raising=False)
    mf.set_backend(mf.MemoryBackend())
    memory_tombstones.clear_all()
    yield mf
    mf.set_backend(None)
    memory_tombstones.clear_all()


def test_a_name_the_child_forgot_is_not_re_added_by_a_curator(env, ledger):
    assert curate(env, "Juno's coach is Ms Vale", "people").status_code == 200           # control: not forgotten -> written
    run(ledger.add(KID, "Ms Lee", actor=KID))
    r = curate(env, "Juno's teacher is Ms Lee", "school")
    assert r.status_code == 409 and "Lee" not in r.text
    assert [x.text for x in rows_of(env, KID)] == ["User's coach is Ms Vale"]
    assert [a[2] for a in curation_audit(env)].count("add") == 2, "refused before the audit: the one earlier add only"


def test_a_forgotten_child_is_not_re_added_by_the_curator_who_forgot_them(env, ledger):
    run(ledger.add(PARENT, "Juno", actor=PARENT))
    assert curate(env, "Juno likes dinosaurs", "likes").status_code == 409
    assert not rows_of(env, KID)
    assert curate(env, "Juno likes dinosaurs", "likes", who=PARENT2).status_code == 200  # control: another curator did not forget


def test_an_edit_cannot_bring_a_forgotten_name_back(env, ledger):
    rid = _curated_teacher(env)
    run(ledger.add(KID, "Mr Brown", actor=KID))
    r = as_(env, PARENT, "admin").post(f"/api/memories/curation/members/{KID}/rows/{rid}/edit",
                                       json=dict(text="Juno's teacher is Mr Brown"), headers=H)
    assert r.status_code == 409
    assert [x.text for x in rows_of(env, KID)] == ["User's teacher is Ms Lee"]


def test_a_ledger_outage_refuses_the_note(env, ledger, monkeypatch):
    class Down(ledger.MemoryBackend):
        async def active_hashes(self, user_id, now_iso):
            raise RuntimeError("ledger down")

    ledger.set_backend(Down())
    assert curate(env, "Juno likes dinosaurs", "likes").status_code == 503             # strict: an outage never writes
    assert not rows_of(env, KID)
    ledger.set_backend(ledger.MemoryBackend())
    assert curate(env, "Juno likes dinosaurs", "likes").status_code == 200             # control: the ledger back -> written


def test_the_curator_path_is_never_an_explicit_re_teach():
    import memory_forgotten as mf

    assert mc.WRITER not in mf.EXPLICIT_RETEACH_WRITERS
    assert not mf.is_explicit_reteach(mc.WRITER, user_id=KID, speaker_verified=True)
    assert mf.is_explicit_reteach("voice_fact", user_id=KID, speaker_verified=True)       # control: the person's own re-teach


_MODEL_IMPORTS = re.compile(r"^\s*(?:import|from)\s+(zoe_core_client|zoe_flue_client|brain_dispatch|expert_dispatch|"
                            r"person_extractor_llm|memory_digest|llm\w*|openai|anthropic)\b", re.M)


def test_d1_no_model_writes_a_curated_fact():
    """D1: what the guardian states is written as stated; nothing in the curation path asks a model to infer a fact."""
    src = (SVC / "memory_curation.py").read_text()
    assert not _MODEL_IMPORTS.search(src)
    assert _MODEL_IMPORTS.search("x = 1\n    import zoe_core_client\n")                  # control: the scan sees an import


def test_d7_a_child_never_sees_or_edits_another_member_entries(env):
    _curated_teacher(env)
    run(env.svc.ingest("User likes trucks.", user_id=KID2, source="chat_regex", status="approved"))
    c = as_(env, KID, "child")
    assert c.get(f"/api/memories/curation/members/{KID2}/rows", headers=H).status_code == 403
    assert c.get(f"/api/memories/curation/members/{KID}/rows", headers=H).status_code == 403
    assert c.get(f"/api/memories/curation/members/{KID}/held", headers=H).status_code == 403
    (row,) = rows_of(env, KID)
    assert run(memory_space.curated_locked(KID, row.metadata)) is True                     # the child cannot edit or forget it
    assert as_(env, PARENT, "admin").get(f"/api/memories/curation/members/{KID}/rows", headers=H).status_code == 200  # control


def test_a_note_that_never_names_a_forgotten_child_is_still_refused(env, ledger):
    run(ledger.add(PARENT, "Juno", actor=PARENT))
    assert curate(env, "Swimming is on Tuesdays", "routines").status_code == 409
    assert not rows_of(env, KID)
    assert curate(env, "Swimming is on Tuesdays", "routines", member=KID2).status_code == 200   # control: another child


def test_the_service_layer_is_flag_dark_and_re_reads_the_curator(env, monkeypatch):
    async def go(curator):
        async with db_compat.get_compat_db() as db:
            return await mc.add(db, curator, KID, "Juno likes dinosaurs", "likes")

    forged = mc.Account(ADULT, "Wren Quill", "admin", "adult")          # claims admin; auth_users says "user"
    with pytest.raises(mc.CurationRefused) as exc:
        run(go(forged))
    assert exc.value.status == 403
    real = mc.Account(PARENT, "Ash Quill", "admin", "adult")
    monkeypatch.delenv("ZOE_MEMORIES_CURATORS")
    with pytest.raises(mc.CurationRefused) as exc:
        run(go(real))
    assert exc.value.status == 404 and not rows_of(env, KID)
    monkeypatch.setenv("ZOE_MEMORIES_CURATORS", "1")
    assert run(go(real))["text"] == "Juno likes dinosaurs"                # control: flag on, a real curator


def test_no_affect_off_rule_for_a_child_own_feelings(env):
    """Emotional-safety note section 4 (owner, 2026-11-03): affective memory stays on for children. Curation refuses only a THIRD
    party's note about someone's feelings; the child's own feelings are stored as for anyone."""
    assert curate(env, "Juno feels sad about the move", "other").status_code == 422
    ref = run(env.svc.ingest("User feels sad about the move.", user_id=KID, source="chat_regex", status="approved"))
    assert ref is not None and ref.metadata["status"] == "approved"
