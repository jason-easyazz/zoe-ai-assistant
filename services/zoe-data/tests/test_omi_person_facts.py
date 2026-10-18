"""Facts about the member's PEOPLE from the pendant day review (owner want 2026-10-18: "it starts building out profiles of people
based on those conversations"). ``omi_day_review`` carries a CONFIRMED voice tag through to the person, proposes ``person_fact`` /
``person_relation`` candidates with provenance, and ``omi_review_apply`` writes them only on the owner's approval.

SYNTHETIC people and lines only (invented names, invented days); the model is a fake that "points" at lines by a substring rule
table, so every test runs the REAL tag -> segments -> pack -> verify -> store -> approve -> forget code, over SQLite with the real
migrations (0045, 0053, 0054, 0055) and a real ``MemoryService`` on a where-honouring Chroma stand-in.

Each owner rule has a test, and each test was run against its negative control (re-run by Edit after touching the rule; the named
test goes red):
  unapproved never written   call ``omi_review_apply.approve`` for every stored candidate at the end of ``_store``
                             -> test_a_tagged_voice_saying_a_fact_about_themself_is_proposed_with_provenance (memory not empty)
  untagged day (D4)          drop the ``unverified_speaker`` branch in ``verify_person_fact`` (treat it like a subject line)
                             -> test_an_untagged_day_never_proposes_a_fact_about_a_person
  unlabelled prompt          make ``wants_people`` return True -> test_an_untagged_day_never_proposes_a_fact_about_a_person
  sensitive (D11)            drop the ``sensitive`` check in ``verify_person_fact`` -> test_sensitive_classes_are_never_proposed
                             drop ``_PERSON_SENSITIVE`` from ``omi_review_apply._floors`` -> test_an_owner_edit_cannot_bring_in_a_sensitive_fact
  subject vs owner           make ``facts_conflict`` return False -> test_a_subject_fact_never_overwrites_an_owner_stated_one (both ways)
  another member             ingest with ``scope="family"`` in ``write_person_fact`` -> test_approval_writes_to_the_owners_layer_only
                             resolve a tag to ANY people row in ``people_index`` (drop ``user_id = ?``) -> test_a_tag_to_someone_elses_person_is_no_person
  forget the recording       skip ``_unwrite`` in ``forget_recording`` -> test_deleting_the_recording_erases_the_facts_it_gave
  forget the person          skip ``_unwrite`` in ``erase_entity`` -> test_forgetting_the_person_erases_their_facts_and_relationship
                             drop ``forget_people`` from the cascade's ``omi_review`` step -> test_forgetting_a_renamed_person_by_id
  forget during approval     drop the rowcount check at the end of ``approve`` -> test_a_forget_that_lands_during_the_write_unwrites_it
  unreadable index           read the conflict scan through ``list_by_entity`` -> test_an_unreadable_memory_index_writes_nothing
  hearsay                    drop the ``hearsay`` check -> test_a_tagged_voice_about_someone_else_is_hearsay
  child                      drop the ``child`` check in ``verify_person_fact`` -> test_no_facts_about_a_child
  child relationship         drop the child check in ``_relation_candidate`` -> test_no_relationship_about_a_child_from_either_side;
                             in ``write_person_relation`` -> test_a_relationship_is_refused_at_approval_if_the_person_became_a_child
  crash window               drop the ``orphans`` lookup in ``unwrite`` / the ``mine`` check in ``write_person_fact`` / the intent
                             write in ``write_person_relation`` -> test_a_write_interrupted_before_its_written_ref_is_still_erased...
  edit off its quote         drop the ``fact_holds`` check in ``approve`` -> test_an_edit_must_still_rest_on_the_quote
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import asyncio
import contextlib
import importlib.util
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import db_compat
import memory_service
import omi_day_review as dr
import omi_review_apply as apply
import omi_voices as ov
import user_erase_gate as erase_gate
from db_pool import _Cursor, _ExecResult
from memory_service import MemoryService
from routers import omi_day_review as dr_router
from test_memory_authority import _Col

SVC = Path(__file__).resolve().parents[1]
A, B = "member-a", "member-b"
DAY = "2026-10-13"
AS_OF = datetime(2026, 10, 13, 23, 59, tzinfo=timezone.utc)
SAM = "Sam Okoro"
PEOPLE_SQL = """
CREATE TABLE people (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT, email TEXT, phone TEXT, birthday TEXT,
    notes TEXT, preferences TEXT, visibility TEXT NOT NULL DEFAULT 'family', deleted INTEGER DEFAULT 0,
    is_partial INTEGER NOT NULL DEFAULT 0, context TEXT, circle TEXT, how_we_met TEXT, first_met_date TEXT,
    introduced_by_person_id TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE person_relationships (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, person_a_id TEXT NOT NULL, person_b_id TEXT NOT NULL, rel_type TEXT NOT NULL,
    rel_a_to_b TEXT NOT NULL, rel_b_to_a TEXT NOT NULL, rel_group TEXT NOT NULL, notes TEXT, valid_from TEXT, valid_to TEXT,
    superseded_by TEXT, authority TEXT, origin TEXT, close_reason TEXT, turn_id TEXT, quote_span TEXT, speaker_rank INTEGER,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE person_important_dates (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, user_id TEXT NOT NULL, label TEXT NOT NULL,
    date_type TEXT NOT NULL DEFAULT 'birthday', month INTEGER, day INTEGER, year INTEGER, reminder_days_before INTEGER NOT NULL DEFAULT 7,
    mem_id TEXT, created_at TEXT);
"""


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

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

    def rows(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]


def _migrate(engine, fname):
    spec = importlib.util.spec_from_file_location(f"mig_{fname[:4]}", SVC / "alembic/versions" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        mod.upgrade()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    db = _Sqlite(path)
    db.conn.executescript(PEOPLE_SQL)
    db.conn.commit()
    engine = sa.create_engine(f"sqlite:///{path}")
    for f in ("0045_omi_recordings.py", "0053_contacts_data_model.py", "0054_omi_day_review.py", "0055_omi_segments_voices.py"):
        _migrate(engine, f)

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    for k, v in {"ZOE_OMI_DAY_REVIEW": "shadow", "ZOE_OMI_SPEAKER_TAG": "shadow", "ZOE_TIMEZONE": "Australia/Perth",
                 "ZOE_OMI_LEARN_VOICES": "0"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    erase_gate.reset()

    async def no_ledger(user_id, text):
        return False
    monkeypatch.setattr(dr, "_forgotten", no_ledger)

    svc = MemoryService(data_dir="/nonexistent/zoe-test-omi-person-facts")
    col = _Col()
    svc._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    erased: list = []

    async def erase_rows(user_id, ids, *, actor, reason=""):
        """The seam: ``erase_rows`` itself (drawers, audit, physical residue) is pinned by the memory suite; here, the owner's rows go."""
        erased.append((user_id, list(ids), reason))
        gone = [i for i in ids if i in col.rows and col.rows[i][1].get("user_id") == user_id]
        for i in gone:
            del col.rows[i]
        return {"rows_removed": len(gone)}

    svc._append_audit = no_audit
    svc.erase_rows = erase_rows
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    who = {"user": {"user_id": A, "role": "member"}}
    app = FastAPI()
    assert dr_router.register(app) is True
    app.dependency_overrides[auth.require_signed_in] = lambda: who["user"]
    yield type("Env", (), {"db": db, "svc": svc, "col": col, "erased": erased, "client": TestClient(app), "who": who})
    dr.set_llm(None)
    erase_gate.reset()


def add_day(env, rid, turns, *, user=A, started="2026-10-13T01:00:00Z"):
    """A transcribed recording with per-turn segments (``omi_voices.store_segments``, as the transcription pass writes them).
    ``turns``: [(text, model_speaker)]. Returns the segment ids in order."""
    env.db.conn.execute("INSERT INTO omi_recordings (id, user_id, device_id, recording_key, started_at, status, transcript, speaker_state, "
                        "created_at) VALUES (?, ?, 'dev', ?, ?, 'done', ?, 'tagged', ?)",
                        (rid, user, rid, started, " ".join(t for t, _ in turns), started))
    env.db.conn.commit()

    async def go():
        ids = await ov.store_segments(env.db, user, rid, [{"start_ms": i * 3000, "end_ms": i * 3000 + 2900, "text": t, "speaker": s}
                                                          for i, (t, s) in enumerate(turns)], model_id="m", channel="pendant")
        await env.db.commit()
        return ids
    return run(go())


def tag(env, sid, name, *, user=A):
    return run(ov.tag(env.db, user, segment_id=sid, name=name))["person_id"]


def fake_model(rules):
    """Points at lines: for each (needle, item) whose needle is in a line it was SHOWN, an item citing that line. Records the prompts."""
    seen: list[str] = []

    def llm(messages, max_tokens):
        seen.append(messages[-1]["content"])
        data = messages[-1]["content"].split("DATA>>>", 1)[1].rsplit("<<<DATA", 1)[0]
        items = []
        for line in data.strip().splitlines():
            m = re.match(r"\[(r\d+\.\d+) [\d:-]+(?: [^\]]+)?\] (.*)$", line)
            for needle, item in rules if m else ():
                if needle in m.group(2):
                    items.append({"ids": [m.group(1)], "quote": needle, "heard_from": "conversation", "about": "me", **item})
        return json.dumps({"items": items})
    llm.seen = seen
    return llm


def review(env, rules, *, user=A):
    llm = fake_model(rules)
    dr.set_llm(llm)
    res = run(dr.review_day(user, DAY, dr.Config(lane="night"), as_of=AS_OF))
    print("REVIEW", res.get("status"), res.get("reason"), res.get("counts"))          # shown by pytest on a failure only
    return res, llm.seen


def cands(env, kind=None, user=A):
    rows = env.db.rows("SELECT * FROM omi_day_candidates WHERE user_id = ? ORDER BY created_at", (user,))
    out = [dict(r, payload=json.loads(r["payload"]), evidence=json.loads(r["evidence"])) for r in rows]
    return [c for c in out if kind is None or c["kind"] == kind]


def rows_of(env, user=A):
    return {i: (d, m) for i, (d, m) in env.col.rows.items() if m.get("user_id") == user}


LIBRARY = "I work at the library in Fremantle now."
LIBRARY_RULE = ("I work at the library in Fremantle", {"kind": "person_fact", "fields": {"name": "Sam", "fact": "works at the library in Fremantle"}})


def sam_day(env, *more, rid="rec1"):
    """The wearer greets; a second voice (tagged Sam Okoro) says LIBRARY; then ``more`` turns."""
    sids = add_day(env, rid, [("Hey, good to see you, how was the week?", "you"), (LIBRARY, "other"), *more])
    return sids, tag(env, sids[1], SAM)


# --- proposing ------------------------------------------------------------------------------------------------------------------
def test_a_tagged_voice_saying_a_fact_about_themself_is_proposed_with_provenance(env):
    sids, pid = sam_day(env)
    res, seen = review(env, [LIBRARY_RULE])
    assert res["status"] == "ok" and res["stored"] == 1, res
    assert f"{SAM}] {LIBRARY}" in seen[0]                                 # the model is told who said it: the member's own tag
    assert "- person_fact:" in seen[0]
    (c,) = cands(env, "person_fact")
    assert c["payload"] == {"person_id": pid, "name": SAM, "fact": "works at the library in Fremantle",
                            "stated_by": "stated_by_subject", "segment_ids": [sids[1]]}
    assert c["status"] == "pending" and c["speaker"] == "someone_else" and c["provenance"] == "pendant"
    (ev,) = c["evidence"]
    assert (ev["recording_id"], ev["segment_id"], ev["speaker"], ev["person_id"]) == ("rec1", sids[1], "someone_else", pid)
    assert ev["quote"] == "I work at the library in Fremantle"
    assert env.col.rows == {}                                              # proposed only: NOTHING is written until the owner says yes
    got = env.client.get("/api/omi/day-review/candidates").json()
    assert [x["kind"] for x in got["candidates"]] == ["person_fact"] and got["pending"] == {"person_fact": 1}


def test_an_untagged_day_never_proposes_a_fact_about_a_person(env):
    add_day(env, "rec1", [("Hey, good to see you, how was the week?", "you"), (LIBRARY, "other")])
    res, seen = review(env, [LIBRARY_RULE])
    assert cands(env, "person_fact") == [] and res["counts"]["drop_unverified_speaker"] == 1
    assert "person_fact" not in seen[0] and "Speakers are NOT labelled" in seen[0]     # D4: an unlabelled day is asked what it always was
    lines = dr.lines_for_day([{"id": "x", "started_at": "2026-10-13T01:00:00Z", "transcript": LIBRARY}])
    assert dr.messages_for(lines)[1]["content"] == dr.USER.format(max_items=dr.MAX_ITEMS_PER_CHUNK, lines="\n".join(map(dr.render, lines)))


def test_sensitive_classes_are_never_proposed(env):
    lines = ["I work at the hospital as a nurse.", "I'm a member of the church choir.", "I have a girlfriend called Jo.",
             "I have a mortgage on the house.", "I play tennis every Saturday morning."]
    sids = add_day(env, "rec1", [("So tell me about yourself.", "you")] + [(t, "other") for t in lines])
    for sid in sids[1:]:
        tag(env, sid, SAM)
    facts = {"I work at the hospital": "works at the hospital as a nurse", "I'm a member of the church choir": "is a member of the church choir",
             "I have a girlfriend called Jo": "has a girlfriend called Jo", "I have a mortgage on the house": "has a mortgage on the house",
             "I play tennis every Saturday": "plays tennis every Saturday"}
    res, _ = review(env, [(q, {"kind": "person_fact", "fields": {"name": "Sam", "fact": f}}) for q, f in facts.items()])
    assert [c["payload"]["fact"] for c in cands(env, "person_fact")] == ["plays tennis every Saturday"]
    assert res["counts"]["drop_sensitive"] == 4                            # D11: counted, never proposed


def test_an_owner_edit_cannot_bring_in_a_sensitive_fact(env):
    sam_day(env)
    review(env, [LIBRARY_RULE])
    (c,) = cands(env, "person_fact")
    r = env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve", json={"fields": {"fact": "votes for the Greens every election"}})
    assert r.status_code in (409, 422) and env.col.rows == {}                      # the quote check refuses it first (422) ...
    sensitive = dict(c, payload=dict(c["payload"], fact="votes for the Greens every election"))
    with pytest.raises(apply.ApplyError) as exc:                                    # ... and the write-time floor on its own (409)
        run(apply._floors(A, sensitive))
    assert exc.value.code == "sensitive"


def test_a_tagged_voice_about_someone_else_is_hearsay(env):
    sids = add_day(env, "rec1", [("How's everyone?", "you"), ("My brother Dev works at the port in Kwinana.", "other")])
    tag(env, sids[1], SAM)
    res, _ = review(env, [("My brother Dev works at the port", {"kind": "person_fact", "fields": {"name": "Dev", "fact": "works at the port"}})])
    assert cands(env) == [] and res["counts"]["drop_hearsay"] == 1


def test_the_model_cannot_add_what_was_not_said_or_a_passing_remark(env):
    sids = add_day(env, "rec1", [("Morning.", "you"), (LIBRARY, "other"), ("I'm going to the beach today with the kids.", "other")])
    for sid in sids[1:]:
        tag(env, sid, SAM)
    res, _ = review(env, [("I work at the library", {"kind": "person_fact", "fields": {"name": "Sam", "fact": "is the head librarian at the state library"}}),
                          ("I'm going to the beach today", {"kind": "person_fact", "fields": {"name": "Sam", "fact": "is going to the beach"}})])
    assert cands(env) == [] and res["counts"]["drop_unsupported"] == 1 and res["counts"]["drop_not_durable"] == 1


def test_no_facts_about_a_child(env):
    sids = add_day(env, "rec1", [("Hi mate.", "you"), ("I play soccer for the under twelves.", "other")])
    pid = tag(env, sids[1], "Max")
    env.db.conn.execute("UPDATE people SET relationship = 'my son' WHERE id = ?", (pid,))
    env.db.conn.commit()
    res, _ = review(env, [("I play soccer for the under twelves", {"kind": "person_fact", "fields": {"name": "Max", "fact": "plays soccer for the under twelves"}})])
    assert cands(env) == [] and res["counts"]["drop_child"] == 1


def test_a_fact_the_wearer_states_about_one_of_their_people(env):
    sids, pid = sam_day(env, ("Sam supports the Dockers, he never misses a game.", "you"))
    run(ov.set_owner_label(env.db, A, sids[2], "you"))                      # the wearer's own word that the line is theirs
    res, _ = review(env, [("Sam supports the Dockers", {"kind": "person_fact", "about": "other", "fields": {"name": "Sam", "fact": "supports the Dockers"}})])
    (c,) = cands(env, "person_fact")
    assert c["payload"]["stated_by"] == "owner_stated" and c["payload"]["person_id"] == pid and c["speaker"] == "you"
    assert c["evidence"][0]["segment_id"] == sids[2]


def test_relationships_to_the_wearer_from_either_side(env):
    sids, pid = sam_day(env, ("I'm your cousin, remember the wedding?", "other"))
    tag(env, sids[2], SAM)
    review(env, [("I'm your cousin", {"kind": "person_fact", "fields": {"name": "Sam", "fact": "is your cousin"}})])
    (c,) = cands(env, "person_relation")
    assert c["payload"]["relation"] == "my cousin" and c["payload"]["stated_by"] == "stated_by_subject"
    out = env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve").json()
    assert out["written_ref"] == f"people:{pid}#relationship"
    assert env.db.rows("SELECT relationship FROM people WHERE id = ?", (pid,))[0]["relationship"] == "my cousin"

    # the wearer, about a known person: "my sister, Lee" -> a relationship for Lee, never a new person
    lee = run(ov.tag(env.db, A, segment_id=add_day(env, "rec2", [("Bye!", "other")], started="2026-10-13T05:00:00Z")[0], name="Lee"))["person_id"]
    sids2 = add_day(env, "rec3", [("I had dinner with my sister, Lee, on the weekend.", "you")], started="2026-10-13T06:00:00Z")
    run(ov.set_owner_label(env.db, A, sids2[0], "you"))
    review(env, [("I had dinner with my sister, Lee", {"kind": "person", "fields": {"name": "Lee", "relation": "sister"}})])
    rel = [c for c in cands(env, "person_relation") if c["payload"]["person_id"] == lee]
    assert [(c["payload"]["relation"], c["payload"]["stated_by"]) for c in rel] == [("my sister", "owner_stated")]
    assert cands(env, "person") == []


def test_a_relationship_never_overwrites_or_lands_on_a_shared_row(env):
    sids, pid = sam_day(env, ("I'm your cousin, remember the wedding?", "other"))
    tag(env, sids[2], SAM)
    review(env, [("I'm your cousin", {"kind": "person_fact", "fields": {"name": "Sam", "fact": "is your cousin"}})])
    (c,) = cands(env, "person_relation")
    env.db.conn.execute("UPDATE people SET relationship = 'my friend' WHERE id = ?", (pid,))
    env.db.conn.commit()
    r = env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve")
    assert r.status_code == 409 and r.json()["detail"] == "held_conflict"
    assert env.db.rows("SELECT relationship FROM people WHERE id = ?", (pid,))[0]["relationship"] == "my friend"
    env.db.conn.execute("UPDATE people SET relationship = NULL, visibility = 'family' WHERE id = ?", (pid,))
    env.db.conn.commit()
    r = env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve")
    assert r.status_code == 409 and r.json()["detail"] == "shared_person"           # every member reads a family row
    assert env.db.rows("SELECT relationship FROM people WHERE id = ?", (pid,))[0]["relationship"] is None


# --- writing (only on approval) -------------------------------------------------------------------------------------------------
def test_approval_writes_to_the_owners_layer_only(env):
    sids, pid = sam_day(env)
    review(env, [LIBRARY_RULE])
    (c,) = cands(env, "person_fact")
    env.who["user"] = {"user_id": B, "role": "admin"}                                    # another member, even an admin
    assert env.client.get(f"/api/omi/day-review/candidates/{c['id']}").status_code == 404
    assert env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve").status_code == 404
    assert env.client.get("/api/omi/day-review/candidates").json()["candidates"] == []
    assert env.col.rows == {}
    env.who["user"] = {"user_id": A, "role": "member"}
    out = env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve", json={"via": "phone"}).json()
    assert out["status"] == "approved" and out["written_ref"].startswith("memory:")
    ((mid, (text, md)),) = rows_of(env).items()
    assert out["written_ref"] == f"memory:{mid}" and text == f"{SAM}: works at the library in Fremantle"
    assert (md["entity_type"], md["entity_id"], md["authority"], md["origin"]) == ("person", pid, "user_confirmed", "omi_review")
    assert md["turn_ref"] == f"omi_segment:{sids[1]}" and md["source_excerpt"] == "I work at the library in Fremantle"
    assert md["candidate_stated_by"] == "stated_by_subject" and sids[1] in md["candidate_segment_ids"] and "rec1" in md["candidate_recording_ids"]
    assert md["candidate_candidate_id"] == c["id"] and md["candidate_approved_via"] == "phone"
    assert md.get("visibility", "personal") == "personal"
    assert [r.text for r in env.svc._metadata_read(A, 50)] == [text]
    assert [r.text for r in env.svc._metadata_read(B, 50)] == []                     # another member never reads it


def test_a_tag_to_someone_elses_person_is_no_person(env):
    sids = add_day(env, "rec1", [("Hey, good to see you, how was the week?", "you"), (LIBRARY, "other")])
    env.db.conn.execute("INSERT INTO people (id, user_id, name, visibility) VALUES ('p-b', ?, 'Sam Okoro', 'family')", (B,))
    env.db.conn.execute("UPDATE omi_segments SET tagged_person_id = 'p-b', tag_source = 'owner' WHERE id = ?", (sids[1],))
    env.db.conn.commit()
    res, seen = review(env, [LIBRARY_RULE])
    assert "Sam Okoro]" not in seen[0] and cands(env) == []                          # B's row is not A's person, whatever the column says
    got = dr.segment_map([{"id": "s1", "recording_id": "r", "label": "person", "tagged_person_id": "p-b", "text": "x"}], (), {})
    assert tuple(got["r"][0]) == ("x", "someone_else", "", "", "s1")


def test_a_subject_fact_never_overwrites_an_owner_stated_one(env):
    sids, pid = sam_day(env)
    owner_row = run(env.svc.ingest(f"{SAM} works at the bakery on High Street", user_id=A, source="voice_fact", confidence=0.9,
                                   entity_type="person", entity_id=pid))
    review(env, [LIBRARY_RULE])
    (c,) = cands(env, "person_fact")
    r = env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve")
    assert r.status_code == 409 and r.json()["detail"] == "held_conflict"
    assert list(rows_of(env)) == [owner_row.id] and rows_of(env)[owner_row.id][1]["status"] == "approved"     # untouched, nothing beside it
    assert cands(env, "person_fact")[0]["status"] == "pending" and cands(env, "person_fact")[0]["write_error"] == "held_conflict"

    # the other way round: an approved subject fact holds back an owner-stated one that contradicts it
    env.col.rows.clear()
    assert env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve").json()["status"] == "approved"
    sids2 = add_day(env, "rec2", [("Sam works at the bakery on High Street these days.", "you")], started="2026-10-13T05:00:00Z")
    run(ov.set_owner_label(env.db, A, sids2[0], "you"))
    review(env, [("Sam works at the bakery on High Street", {"kind": "person_fact", "about": "other",
                                                            "fields": {"name": "Sam", "fact": "works at the bakery on High Street"}})])
    (o,) = [x for x in cands(env, "person_fact") if x["payload"]["stated_by"] == "owner_stated"]
    r = env.client.post(f"/api/omi/day-review/candidates/{o['id']}/approve")
    assert r.status_code == 409 and r.json()["detail"] == "held_conflict"
    ((_, (text, _md)),) = rows_of(env).items()
    assert text == f"{SAM}: works at the library in Fremantle"


def test_a_compatible_fact_is_added_and_the_same_fact_is_linked_not_written_twice(env):
    sids, pid = sam_day(env, ("I play tennis every Saturday morning.", "other"))
    tag(env, sids[2], SAM)
    run(env.svc.ingest(f"{SAM}: works at the library in Fremantle on weekdays", user_id=A, source="voice_fact", confidence=0.9,
                       entity_type="person", entity_id=pid))
    review(env, [LIBRARY_RULE, ("I play tennis every Saturday", {"kind": "person_fact", "fields": {"name": "Sam", "fact": "plays tennis every Saturday"}})])
    by = {c["payload"]["fact"]: c for c in cands(env, "person_fact")}
    same = env.client.post(f"/api/omi/day-review/candidates/{by['works at the library in Fremantle']['id']}/approve").json()
    assert same["status"] == "approved" and same["written_ref"].startswith("known:memory:")
    new = env.client.post(f"/api/omi/day-review/candidates/{by['plays tennis every Saturday']['id']}/approve").json()
    assert new["written_ref"].startswith("memory:") and len(rows_of(env)) == 2


def test_the_conflict_rule():
    assert dr.facts_conflict("works at the library", "Sam Okoro: works at the bakery", "Sam Okoro")
    assert dr.facts_conflict("likes coffee", "doesn't like coffee") and dr.facts_conflict("loves coffee", "hates coffee")
    assert dr.facts_conflict("lives in Fremantle", "Sam lives in Bunbury", "Sam")
    assert not dr.facts_conflict("works at the library", "plays tennis on Saturdays")
    assert not dr.facts_conflict("plays tennis", "plays golf")                       # not single-valued: both can be true
    assert not dr.facts_conflict("works at the library", "works at the library in Fremantle")
    assert dr.same_fact("works at the library in Fremantle", "Sam: works at the library in Fremantle on weekdays", "Sam")
    assert not dr.same_fact("likes coffee", "doesn't like coffee")


# --- forgetting -----------------------------------------------------------------------------------------------------------------
def _approved_fact(env):
    sids, pid = sam_day(env)
    review(env, [LIBRARY_RULE])
    (c,) = cands(env, "person_fact")
    assert env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve").json()["status"] == "approved"
    assert len(rows_of(env)) == 1
    return c, pid


def test_deleting_the_recording_erases_the_facts_it_gave(env):
    c, _pid = _approved_fact(env)
    assert run(dr.forget_recording(A, "rec1")) == 1
    assert rows_of(env) == {} and cands(env) == []                                  # the derived fact AND its candidate are gone
    assert env.erased and env.erased[-1][0] == A and "Sam" not in env.erased[-1][2]   # the reason never carries the name


def test_forgetting_the_person_erases_their_facts_and_relationship(env):
    import memory_forgotten
    c, pid = _approved_fact(env)
    sids = add_day(env, "rec2", [("I'm your cousin, remember the wedding?", "other")], started="2026-10-13T05:00:00Z")
    tag(env, sids[0], SAM)
    review(env, [("I'm your cousin", {"kind": "person_fact", "fields": {"name": "Sam", "fact": "is your cousin"}})])
    (rc,) = cands(env, "person_relation")
    env.client.post(f"/api/omi/day-review/candidates/{rc['id']}/approve")
    assert env.db.rows("SELECT relationship FROM people WHERE id = ?", (pid,))[0]["relationship"] == "my cousin"
    env.col.rows.update({"other-row": ("Pat: likes jazz", {"user_id": A, "status": "approved"})})
    assert run(dr.erase_entity(A, memory_forgotten.name_pattern("Sam Okoro"), strict=True)) == 2
    assert list(rows_of(env)) == ["other-row"] and cands(env) == []
    assert env.db.rows("SELECT relationship FROM people WHERE id = ?", (pid,))[0]["relationship"] is None


def test_forgetting_a_renamed_person_by_id(env):
    import memory_forget_cascade as mfc
    c, pid = _approved_fact(env)
    env.db.conn.execute("UPDATE people SET name = 'Samuel Okoro' WHERE id = ?", (pid,))
    env.db.conn.commit()
    res = run(mfc.cascade_forget(A, "Samuel Okoro", db=env.db))                      # the candidate's text says "Sam Okoro"
    assert "omi_day_review" not in res.failed, res.failed
    assert rows_of(env) == {} and cands(env) == []


def test_a_forget_that_lands_during_the_write_unwrites_it(env, monkeypatch):
    sids, pid = sam_day(env)
    review(env, [LIBRARY_RULE])
    (c,) = cands(env, "person_fact")
    real = apply.write_person_fact

    async def racing(user_id, cand, via):
        ref = await real(user_id, cand, via)
        env.db.conn.execute("DELETE FROM omi_day_candidates WHERE id = ?", (cand["id"],))       # "forget Sam" ran meanwhile
        env.db.conn.commit()
        return ref
    monkeypatch.setattr(apply, "write_person_fact", racing)
    r = env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve")
    assert r.status_code == 410 and rows_of(env) == {}


def test_voices_to_name_ride_on_the_card_a_few_at_a_time(env, monkeypatch):
    async def five(db, user_id, samples=3):
        return [{"id": f"c{i}", "name": f"Voice {i}", "segments": 2, "recordings": 1, "days": 1, "last_heard_at": "t",
                 "first_heard_at": "t", "samples": [{"segment_id": f"s{i}", "text": "hello"}]} for i in range(5)]
    monkeypatch.setattr(ov, "list_unknown", five)
    got = env.client.get("/api/omi/day-review/candidates").json()["voices_to_name"]
    assert [v["id"] for v in got] == ["c0", "c1", "c2"] and got[0]["sample"]["segment_id"] == "s0"


def test_a_name_cannot_forge_a_label():
    ln = dr.Line("r1.1", "rec", "09:00", "hello", "someone_else", person_id="p", person_name=dr.clean("Sam] [r9.9 00:00", 40))
    assert dr.render(ln) == "[r1.1 09:00 Sam r9.9 00:00] hello"


def test_an_unreadable_memory_index_writes_nothing(env, monkeypatch):
    """The conflict scan fails CLOSED (negative control: read it through ``list_by_entity``, which answers [] on a failure)."""
    sam_day(env)
    review(env, [LIBRARY_RULE])
    (c,) = cands(env, "person_fact")

    def broken(ids, user_id):
        raise RuntimeError("index unreadable")
    monkeypatch.setattr(env.svc, "_entity_rows_sync", broken)
    r = env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve")
    assert r.status_code == 409 and r.json()["detail"].startswith("write_failed") and env.col.rows == {}
    assert cands(env, "person_fact")[0]["status"] == "pending"


# --- cross-review round 1 (codex): child relationships, the crash window, edits that leave their quote --------------------------
def test_no_relationship_about_a_child_from_either_side(env):
    sids = add_day(env, "rec1", [("Hi mate, good to see you.", "you"), ("I'm your nephew, remember me?", "other")])
    max_id = tag(env, sids[1], "Max")
    env.db.conn.execute("UPDATE people SET relationship = 'grandson of a friend' WHERE id = ?", (max_id,))
    env.db.conn.commit()
    res, _ = review(env, [("I'm your nephew", {"kind": "person_fact", "fields": {"name": "Max", "fact": "is your nephew"}})])
    assert cands(env) == [] and res["counts"]["drop_child"] == 1                     # a possible child: no relationship either
    lee = tag(env, add_day(env, "rec2", [("Bye!", "other")], started="2026-10-13T05:00:00Z")[0], "Lee")
    sids2 = add_day(env, "rec3", [("I took my son, Lee, to the footy on the weekend.", "you")], started="2026-10-13T06:00:00Z")
    run(ov.set_owner_label(env.db, A, sids2[0], "you"))
    res, _ = review(env, [("I took my son, Lee", {"kind": "person", "fields": {"name": "Lee", "relation": "son"}})])
    assert [c for c in cands(env) if c["payload"].get("person_id") == lee] == [] and res["counts"]["drop_child"] == 1


def test_a_relationship_is_refused_at_approval_if_the_person_became_a_child(env):
    sids, pid = sam_day(env, ("I'm your cousin, remember the wedding?", "other"))
    tag(env, sids[2], SAM)
    review(env, [("I'm your cousin", {"kind": "person_fact", "fields": {"name": "Sam", "fact": "is your cousin"}})])
    (c,) = cands(env, "person_relation")
    env.db.conn.execute("UPDATE people SET birthday = '2019-05-01' WHERE id = ?", (pid,))
    env.db.conn.commit()
    r = env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve")
    assert r.status_code == 409 and r.json()["detail"] == "child"
    assert env.db.rows("SELECT relationship FROM people WHERE id = ?", (pid,))[0]["relationship"] is None


def _claim(env, cid):
    """What ``approve`` does before the write: the candidate is ``approving`` (a crash would leave it so)."""
    env.db.conn.execute("UPDATE omi_day_candidates SET status = 'approving', decided_at = '2000-01-01T00:00:00Z' WHERE id = ?", (cid,))
    env.db.conn.commit()
    return dr.public(env.db.conn.execute("SELECT * FROM omi_day_candidates WHERE id = ?", (cid,)).fetchone())


def test_a_write_interrupted_before_its_written_ref_is_still_erased_and_never_doubled(env):
    """The crash window: the memory row / relationship exists, the candidate is still ``approving`` with no ``written_ref``.
    Negative controls: skip the ``orphans`` lookup in ``unwrite``; drop the intent write in ``write_person_relation``; drop the
    ``mine`` check in ``write_person_fact`` (the retry links ``known:`` and the row becomes unerasable)."""
    sids, pid = sam_day(env, ("I'm your cousin, remember the wedding?", "other"))
    tag(env, sids[2], SAM)
    review(env, [LIBRARY_RULE, ("I'm your cousin", {"kind": "person_fact", "fields": {"name": "Sam", "fact": "is your cousin"}})])
    fact, rel = cands(env, "person_fact")[0], cands(env, "person_relation")[0]
    run(apply.write_person_fact(A, _claim(env, fact["id"]), "desktop"))              # ... and the process died here
    run(apply.write_person_relation(A, _claim(env, rel["id"]), "desktop"))
    assert len(rows_of(env)) == 1 and env.db.rows("SELECT relationship FROM people WHERE id = ?", (pid,))[0]["relationship"] == "my cousin"
    (only,) = rows_of(env)
    out = env.client.post(f"/api/omi/day-review/candidates/{fact['id']}/approve").json()     # the stale claim is retaken
    assert out["written_ref"] == f"memory:{only}" and list(rows_of(env)) == [only]
    env.db.conn.execute("UPDATE omi_day_candidates SET written_ref = NULL, status = 'approving' WHERE id = ?", (fact["id"],))
    env.db.conn.commit()
    assert run(dr.forget_recording(A, "rec1")) == 2
    assert rows_of(env) == {} and cands(env) == []
    assert env.db.rows("SELECT relationship FROM people WHERE id = ?", (pid,))[0]["relationship"] is None


def test_an_edit_must_still_rest_on_the_quote(env):
    sam_day(env)
    review(env, [LIBRARY_RULE])
    (c,) = cands(env, "person_fact")
    r = env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve", json={"fields": {"fact": "owns the library"}})
    assert r.status_code == 422 and r.json()["detail"] == "edit_unsupported" and env.col.rows == {}
    assert cands(env, "person_fact")[0]["status"] == "pending"
    out = env.client.post(f"/api/omi/day-review/candidates/{c['id']}/approve", json={"fields": {"fact": "works at the library"}}).json()
    assert out["status"] == "approved"
    ((_, (text, md)),) = rows_of(env).items()
    assert text == f"{SAM}: works at the library" and md["candidate_edited_by_owner"] in (True, "True", "true", 1)


# --- OD5 (owner 2026-10-19): searching what other people said - words only, privately to the wearer ---------------------------
def test_searching_what_people_said_is_words_only_and_private(env):
    """Negative controls: drop ``s.user_id = ? AND r.user_id = ?`` from ``search_said`` (member B finds A's lines); drop the
    ``not_person IS NULL`` filter (the TV line comes back)."""
    from routers import omi_recordings as omi_rec_router
    from routers import omi_voices as voices_router
    app = FastAPI()
    app.include_router(voices_router.router)
    app.dependency_overrides[omi_rec_router._require_owner] = lambda: env.who["user"]
    client = TestClient(app)
    sids, pid = sam_day(env, ("We booked the Bali trip for March.", "other"), ("Bali trip special, book now on channel nine!", "other"))
    tag(env, sids[2], SAM)
    run(ov.mark_not_a_person(env.db, A, kind="tv", segment_id=sids[3]))
    got = client.get("/api/omi/segments/search", params={"q": "bali trip"}).json()["lines"]
    assert [(x["text"], x["label"], x["person_name"]) for x in got] == [("We booked the Bali trip for March.", "person", SAM)]
    assert set(got[0]) == {"segment_id", "recording_id", "started_at", "start_ms", "end_ms", "text", "label", "person_id", "person_name"}
    by_person = client.get("/api/omi/segments/search", params={"person_id": pid}).json()["lines"]
    assert {x["segment_id"] for x in by_person} == {sids[1], sids[2]}
    assert client.get("/api/omi/segments/search", params={"q": "%"}).status_code == 400            # no words: refused, not "everything"
    env.who["user"] = {"user_id": B, "role": "admin"}
    assert client.get("/api/omi/segments/search", params={"q": "bali"}).json()["lines"] == []       # another member, even an admin
    assert client.get("/api/omi/segments/search", params={"person_id": pid}).status_code == 404     # and A's person is not B's
