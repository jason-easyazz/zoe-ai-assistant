"""Two-fact questions: no age decay on a durable fact the owner stated, and a bounded second hop (ZMB L1 / L2).

Measured on Zoe's real embedder (Chroma + MiniLM) on 2026-10-07: 14 of 20 two-fact questions, dates 10 of 10, joins 4 of 10 - the first
fact is found, the second is not. Two causes, two fixes, each pinned here:

* ``_semantic_search``'s 70-day recency half-life applied to EVERY row, so the older of two facts the owner told Zoe (a birthday said
  three weeks ago) lost to last week's chatter; a fact the owner stated is durable (``memory_service._durable_user_fact``);
* a question that needs two facts ("is Dana's birthday before my dentist appointment", "who in my family lives near Rowan's school") was
  ONE search; ``multi_hop_recall.expand`` searches each subject of a comparison on its own and follows the entity the first fact names.

Synthetic names; no network, no model, no live store (``ci_safe``). The bench cells (L1 / L2, controls, ablations) are in ``test_zmb_lab.py``.
"""
from __future__ import annotations

import asyncio
import datetime
import time
import types

import pytest

import memory_service
import multi_hop_recall as mh
from memory_service import MemoryRef, MemoryService

pytestmark = pytest.mark.ci_safe


def run(coro):
    return asyncio.run(coro)


def ref(i, text, **md):
    return MemoryRef(id=i, text=text, metadata={"user_id": "u", **md})


# ── the shapes ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q, clauses", [
    ("is Dana's birthday before my dentist appointment", ["Dana's birthday", "my dentist appointment"]),
    ("Is Leo older than Dana?", ["Leo", "Dana"]),
    ("does my physio come after my dentist appointment", ["my physio come", "my dentist appointment"]),
])
def test_a_comparison_names_two_subjects(q, clauses):
    assert mh.subject_clauses(q) == clauses


@pytest.mark.parametrize("q", ["where does Dana live", "what's my dentist appointment", "what time is it", "is it before", ""])
def test_any_other_question_is_not_a_comparison(q):
    assert mh.subject_clauses(q) == []


@pytest.mark.parametrize("q, yes", [
    ("who in my family lives near Rowan's school", True), ("who lives close to Dana", True), ("who is in the same town as Leo", True),
    ("where does Dana live", False), ("what is Rowan's school", False),
])
def test_a_relational_question_is_recognised(q, yes):
    assert mh.is_relational(q) is yes


def test_the_bridge_follows_the_entity_the_first_fact_names_and_only_for_the_questions_own_names():
    rows = [ref("a", "User's child Rowan's school is in Marlowby."), ref("z", "User's friend Priya lives in Oldmere."),
            ref("b", "User's sister Tamsin lives in Marlowby.")]
    assert mh.bridge_entities("who in my family lives near Rowan's school", rows) == ["Marlowby"]    # not Oldmere: Priya is not about Rowan
    assert mh.bridge_entities("who in my family lives near my school", rows) == []                  # a question that names nothing has no bridge


# ── the search ───────────────────────────────────────────────────────────────

class FakeSearch:
    def __init__(self, table):
        self.table = table
        self.calls = []

    async def __call__(self, q, *, limit=4):
        self.calls.append((q, limit))
        return [r for key, rows in self.table.items() if key.lower() in q.lower() for r in rows][:limit]


def test_each_subject_of_a_comparison_is_searched_on_its_own_and_the_rows_are_united():
    dob = ref("dob", "Dana's birthday is on 14 March.")
    appt = ref("appt", "User's dentist appointment is on 9 March.")
    noise = [ref(f"n{i}", f"User's chatter number {i} about the garden.") for i in range(6)]
    first = noise + []
    search = FakeSearch({"Dana": [dob], "dentist": [appt]})
    got = run(mh.expand(search, "is Dana's birthday before my dentist appointment", first, limit=6))
    ids = [r.id for r in got]
    assert ids == ["n0", "n1", "n2", "n3", "dob", "appt"]                               # room is short: the first rows keep their head, the second hop displaces the TAIL
    assert len(set(ids)) == len(ids)
    assert len(search.calls) == 2                                                        # one search per subject: bounded


def test_a_join_goes_through_the_entity_the_first_fact_names():
    a = ref("a", "User's child Rowan's school is in Marlowby.")
    b = ref("b", "User's sister Tamsin lives in Marlowby.")
    search = FakeSearch({"Marlowby": [a, b], "Rowan": [a]})
    got = run(mh.expand(search, "who in my family lives near Rowan's school", [a], limit=8))
    assert [r.id for r in got] == ["a", "b"]
    assert [c[0] for c in search.calls] == ["Marlowby"]


def test_a_bridge_row_must_actually_mention_the_entity():
    a = ref("a", "User's child Rowan's school is in Marlowby.")
    stray = ref("x", "User's dentist appointment is on 9 March.")
    search = FakeSearch({"Marlowby": [stray]})
    assert [r.id for r in run(mh.expand(search, "who in my family lives near Rowan's school", [a], limit=8))] == ["a"]


def test_every_other_question_is_untouched_and_the_flag_turns_it_off(monkeypatch):
    first = [ref("a", "x one"), ref("b", "y two")]
    search = FakeSearch({"anything": [ref("c", "c")]})
    assert run(mh.expand(search, "where does Dana live", first, limit=8)) == first and search.calls == []
    monkeypatch.setenv(mh.ENV, "off")
    assert run(mh.expand(search, "is Dana's birthday before my dentist appointment", first, limit=8)) == first and search.calls == []


def test_a_slow_or_failing_search_costs_the_second_hop_never_the_answer(monkeypatch):
    first = [ref("a", "User's child Rowan's school is in Marlowby.")]

    async def slow(q, *, limit=4):
        await asyncio.sleep(5)
        return []

    async def boom(q, *, limit=4):
        raise RuntimeError("store down")
    monkeypatch.setattr(mh, "BUDGET_S", 0.05)
    t0 = time.monotonic()
    assert run(mh.expand(slow, "who in my family lives near Rowan's school", first, limit=8)) == first
    assert time.monotonic() - t0 < 1.0
    assert run(mh.expand(boom, "who in my family lives near Rowan's school", first, limit=8)) == first


# ── no age decay on a durable fact the owner stated ──────────────────────────

class _Col:
    """A collection that answers every query with the same two rows at (nearly) the same distance."""

    def __init__(self, rows):
        self.rows = rows

    def query(self, *, query_texts, n_results, include=None, where=None):
        rows = self.rows[:n_results]
        return {"ids": [[r[0] for r in rows]], "documents": [[r[1] for r in rows]],
                "metadatas": [[r[2] for r in rows]], "distances": [[r[3] for r in rows]]}


def _search(rows):
    svc = MemoryService(data_dir="/nonexistent/zoe-test-multi-hop")
    col = _Col(rows)
    svc._collection = lambda: col
    return [r.id for r in svc._semantic_search("dentist appointment date", "u", 2)]


def _row(i, text, days_old, cls, dist, mtype="fact"):
    added = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_old)).isoformat()
    return (i, text, {"user_id": "u", "status": "approved", "added_at": added, "confidence": 0.9, "memory_type": mtype,
                      "authority_class": cls, "origin": "x"}, dist)


def test_an_old_fact_the_owner_stated_keeps_its_rank_against_a_newer_near_miss(monkeypatch):
    rows = [_row("old", "User's dentist appointment is on 9 March.", 200, "user_stated", 0.50),
            _row("new", "User's physio appointment is on 2 April.", 1, "user_stated", 0.55)]
    assert _search(rows)[0] == "old"                                          # durable: the closer row wins whatever its age
    monkeypatch.setenv("ZOE_RECALL_DURABLE_NO_DECAY", "0")
    assert _search(rows)[0] == "new"                                          # CONTROL: the old half-life buries it (the measured defect)


def test_a_model_written_row_and_a_mood_still_decay(monkeypatch):
    rows = [_row("old_model", "User's dentist appointment is on 9 March.", 200, "model_from_transcript", 0.50),
            _row("new_model", "User's physio appointment is on 2 April.", 1, "model_from_transcript", 0.55)]
    assert _search(rows)[0] == "new_model"
    moods = [_row("old_mood", "User felt anxious about the dentist appointment.", 200, "user_stated", 0.50, mtype="emotional_moment"),
             _row("new_mood", "User felt calm about the physio appointment.", 1, "user_stated", 0.55, mtype="emotional_moment")]
    assert _search(moods)[0] == "new_mood"


def test_durable_user_fact_reads_the_class_and_never_raises():
    assert memory_service._durable_user_fact({"authority_class": "user_stated"}, "x") is True
    assert memory_service._durable_user_fact({"authority_class": "user_confirmed"}, "x") is True
    assert memory_service._durable_user_fact({"authority_class": "user_stated_derived"}, "x") is False        # a paraphrase of the owner is a model's
    assert memory_service._durable_user_fact({"authority_class": "user_stated_derived", "authority_basis": "verbatim_user_span"}, "x") is True
    assert memory_service._durable_user_fact({"authority_class": "model_from_turn"}, "x") is False
    assert memory_service._durable_user_fact({}, "") is False
    assert memory_service._durable_user_fact(None, "") is False                                                 # never raises


# ── an empty first hop (PR #1911 review round 2) ─────────────────────────────

def test_a_comparison_whose_whole_sentence_missed_still_searches_each_subject():
    dob = ref("dob", "Dana's birthday is on 14 March.")
    appt = ref("appt", "User's dentist appointment is on 9 March.")
    search = FakeSearch({"Dana": [dob], "dentist": [appt]})
    got = run(mh.expand(search, "is Dana's birthday before my dentist appointment", [], limit=4))
    assert [r.id for r in got] == ["dob", "appt"] and len(search.calls) == 2
    # a relational question has nothing to bridge from with no first fact: unchanged, no search
    search = FakeSearch({"Marlowby": [ref("b", "User's sister Tamsin lives in Marlowby.")]})
    assert run(mh.expand(search, "who in my family lives near Rowan's school", [], limit=4)) == [] and search.calls == []


def test_the_packet_endpoint_runs_the_second_hop_when_the_first_search_returns_nothing(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import auth
    from routers import memories as memories_mod
    dob = ref("dob", "Dana's birthday is on 14 March.")
    appt = ref("appt", "User's dentist appointment is on 9 March.")
    question = "is Dana's birthday before my dentist appointment"

    class FakeSvc:
        async def load_for_prompt(self, user_id, *, limit=20):
            return []

        async def search(self, q, *, user_id, limit=10, **_kw):
            if q == question:
                return []                                   # the whole sentence misses
            return [dob] if "Dana" in q else [appt] if "dentist" in q else []

        async def load_recent_for_prompt(self, user_id, **kw):
            return []

    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda uid: False)
    monkeypatch.setattr(memories_mod, "_svc", lambda: FakeSvc())
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    app = FastAPI()
    app.include_router(memories_mod.router)
    r = TestClient(app).get("/api/memories/for-prompt", headers={"X-Internal-Token": "tok"},
                            params={"user_id": "u", "message": question, "limit": "12"})
    assert r.status_code == 200
    assert "14 March" in r.json()["packet"] and "9 March" in r.json()["packet"]
