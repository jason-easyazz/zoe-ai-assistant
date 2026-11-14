"""The guarded claim writer (memory redesign step 6, ``memory_claims_apply`` + ``memory_claims_render``): one test per rule
with a negative control beside it. A REAL ``MemoryService`` on an in-memory collection, so ``review(edit)`` / ``ingest`` and
their walls are the production ones. Synthetic people, fixed dates. ``ci_safe``."""
from __future__ import annotations

import asyncio
import logging
import sys
import types
from dataclasses import replace

import pytest

import memory_authority as auth
import memory_claims as mc
import memory_claims_apply as ap
import memory_claims_render as render
import memory_claims_validate as vm
import memory_service
import medical_safety
import people_graph
import user_erase_gate
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "demo_mc_w"
AT = people_graph.parse_ts("2026-11-14T09:00:00+00:00")
JUNO = {"kind": "person", "id": "p1"}
GRAPH = vm.Graph([vm.Person("p1", "Juno", "daughter")], [("p1", "daughter")])
ROW, SAID = "User's daughter Juno is 7 years old.", "juno is 8 not 7"
CLAIM = {"type": "correction", "turn": 0, "quote": SAID, "subject": "Juno", "attribute": "age", "old": "7", "new": "8",
         "time": "current"}


@pytest.fixture
def svc(monkeypatch):
    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_MEMORY_CLAIMS", "ZOE_MEDICAL_SAFETY", "ZOE_AFFECT_CONSENT_GATE"):
        monkeypatch.delenv(k, raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-memory-claims-apply")
    s._col = _Col()
    s._collection = lambda: s._col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    user_erase_gate.reset()
    ap.STATS.clear()
    return s


class Walls:
    def __init__(self, retracted=False, forgotten=False):
        self.r, self.f, self.asked = retracted, forgotten, []

    def retracted(self, _user, _text):
        return self.r

    async def forgotten(self, _user, text):
        self.asked.append(text)
        if isinstance(self.f, Exception):
            raise self.f
        return self.f


class Registry:
    def __init__(self):
        self.calls = []

    async def record(self, user_id, entries, *, generation, row_ids):
        self.calls.append((user_id, tuple(entries), generation, dict(row_ids)))
        return len(entries)


def attr(text, ref, attribute, piece):
    i = text.index(piece)
    n = mc.normalise(attribute, piece)
    return vm.Attr(ref, attribute, n.value, n.unit, f"{i}:{i + len(piece)}")


def seed(svc, text, source="voice_fact"):
    return asyncio.run(svc.ingest(text, user_id=UID, source=source, confidence=0.9)).id


def setup(svc, *, row=ROW, said=SAID, claim=CLAIM, piece="7 years old", ref=JUNO, attribute="age", source="voice_fact",
          graph=GRAPH):
    """Seed one row, validate ``claim`` against it and return ``(verdict, parsed claim, job, row id)``."""
    rid = seed(svc, row, source)
    rows = [vm.Row(rid, row, attrs=(attr(row, ref, attribute, piece),))]
    turns = [vm.Turn(0, AT, said, people_graph.content_turn_id(UID, said))]
    ctx = vm.Context(UID, turns, rows, graph, detector=medical_safety.detect)
    parsed = mc.parse_output({"claims": [claim], "abstain": None})
    verdict = vm.validate(parsed, ctx)[0]
    return verdict, parsed.items[0], ap.Job(UID, turns, rows, people={"p1": ("Juno", "daughter")}), rid


def run(verdict, claim, job, svc, **kw):
    kw.setdefault("walls", Walls())
    kw.setdefault("registry", Registry())
    kw.setdefault("fix_date", None)
    return asyncio.run(ap.apply(verdict, claim, job, svc=svc, **kw))


def approved(svc):
    return sorted(d for d, m in svc._col.rows.values() if m.get("status") == "approved")


# -- the flag ------------------------------------------------------------------------------------------------------

def test_the_flag_is_off_by_default_and_off_touches_nothing(svc, monkeypatch):
    v, c, job, _rid = setup(svc)
    before = dict(svc._col.rows)
    assert ap.mode() == "off"
    assert asyncio.run(ap.apply(v, c, job, svc=svc)).code == ap.C_OFF and svc._col.rows == before
    monkeypatch.setenv("ZOE_MEMORY_CLAIMS", "shadow")
    assert ap.mode() == "shadow"
    monkeypatch.setenv("ZOE_MEMORY_CLAIMS", "enforce")          # not an allowed value: the declared default
    assert ap.mode() == "off"


def test_shadow_computes_the_write_and_writes_nothing(svc):
    v, c, job, rid = setup(svc)
    before, reg = {k: (d, dict(m)) for k, (d, m) in svc._col.rows.items()}, Registry()
    out = run(v, c, job, svc, mode_="shadow", registry=reg)
    assert (out.code, out.rows, out.registry) == (ap.C_WOULD, 1, 2)
    assert [(p.row_id, p.new_text) for p in out.plans] == [(rid, "User's daughter Juno is 8 years old.")]
    assert svc._col.rows == before and reg.calls == []
    # control: the same call with ``on`` does change the store
    assert run(v, c, job, svc, mode_="on").code == ap.C_WRITTEN and svc._col.rows != before


# -- write, read back, then record ----------------------------------------------------------------------------------

def test_on_replaces_the_row_keeps_its_authority_and_records_after_the_read_back(svc):
    v, c, job, rid = setup(svc)
    old_rank, reg = auth.row_rank(svc._col.rows[rid][1], ROW), Registry()
    out = run(v, c, job, svc, mode_="on", registry=reg)
    assert (out.code, out.rows, out.registry) == (ap.C_WRITTEN, 1, 2)
    assert approved(svc) == ["User's daughter Juno is 8 years old."]
    assert svc._col.rows[rid][1]["status"] == "superseded"
    text, meta = svc._col.rows[out.new_ids[0]]
    assert auth.row_rank(meta, text) >= old_rank and meta["supersedes_id"] == rid
    (a,) = ap.attrs_of(meta)
    assert a.value_norm == {"v": 8.0} and mc.span_value("age", text, a.span).value == {"v": 8.0}
    (user, entries, _gen, ids), = reg.calls
    assert user == UID and {e.role for e in entries} == {"superseded", "asserted"} and ids == {rid: out.new_ids[0]}


def test_a_row_that_does_not_read_back_is_reported_and_nothing_is_recorded(svc):
    v, c, job, _rid = setup(svc)
    real, reg = svc.get, Registry()

    async def wrong(mem_id):
        got = await real(mem_id)
        return got if got is None or got.text == ROW else types.SimpleNamespace(id=got.id, text="", metadata=got.metadata)

    svc.get = wrong
    out = run(v, c, job, svc, mode_="on", registry=reg)
    assert out.code == ap.C_VERIFY and reg.calls == []


# -- never without a validated claim --------------------------------------------------------------------------------

@pytest.mark.parametrize("verdict", ["abstain", "hold", "refuse", "agree"])
def test_anything_but_an_apply_verdict_writes_nothing(svc, verdict):
    v, c, job, _rid = setup(svc)
    before = dict(svc._col.rows)
    forged = replace(v, verdict=verdict, rule=9)               # the planned edits are still attached
    assert run(forged, c, job, svc, mode_="on").code == ap.C_NOT_VALIDATED and svc._col.rows == before
    assert run(v, c, job, svc, mode_="on").code == ap.C_WRITTEN        # control: the verdict is what stopped it


def test_a_claim_the_validator_abstains_on_reaches_no_row(svc):
    graph = vm.Graph([vm.Person("p1", "Ana-Lise", "daughter")])
    v, c, job, _rid = setup(svc, row="User's daughter Ana-Lise is 6 years old.", said="ana is 7 not 6", piece="6 years old",
                            claim={**CLAIM, "quote": "ana is 7 not 6", "subject": "Ana", "old": "6", "new": "7"}, graph=graph)
    assert (v.verdict, v.rule) == ("abstain", 4)
    assert run(v, c, job, svc, mode_="on").code == ap.C_NOT_VALIDATED
    assert approved(svc) == ["User's daughter Ana-Lise is 6 years old."]


# -- authority is never lowered -------------------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [auth.USER_CONFIRMED, auth.OPERATOR, auth.CURATED])
def test_a_confirmed_operator_or_curated_row_is_never_replaced(svc, cls):
    v, c, job, rid = setup(svc)
    text, meta = svc._col.rows[rid]
    svc._col.rows[rid] = (text, {**meta, "authority_class": cls, "authority": auth.authority_of(cls)})
    before = dict(svc._col.rows)
    assert run(v, c, job, svc, mode_="on").code == ap.C_AUTHORITY and svc._col.rows == before
    assert run(v, c, job, svc, mode_="shadow").code == ap.C_AUTHORITY      # shadow reports the same refusal


@pytest.mark.parametrize("cls", [auth.USER_STATED, auth.USER_STATED_DERIVED, auth.MODEL_FROM_TRANSCRIPT])
def test_control_a_row_at_or_below_the_members_own_words_is_replaced(svc, cls):
    v, c, job, rid = setup(svc)
    text, meta = svc._col.rows[rid]
    svc._col.rows[rid] = (text, {**meta, "authority_class": cls, "authority": auth.authority_of(cls)})
    out = run(v, c, job, svc, mode_="on")
    assert out.code == ap.C_WRITTEN
    new_text, new_meta = svc._col.rows[out.new_ids[0]]
    assert auth.row_rank(new_meta, new_text) >= auth.RANK[cls]


# -- the forget walls, fail closed ----------------------------------------------------------------------------------

@pytest.mark.parametrize("walls,code", [
    (Walls(retracted=True), ap.C_RETRACTED),
    (Walls(forgotten=True), ap.C_FORGOTTEN),
    (Walls(forgotten=OSError("ledger down")), ap.C_LEDGER),
])
def test_a_forget_wall_or_an_unreadable_ledger_stops_the_write(svc, walls, code):
    v, c, job, _rid = setup(svc)
    before = dict(svc._col.rows)
    for m in ("shadow", "on"):
        assert run(v, c, job, svc, mode_=m, walls=walls).code == code and svc._col.rows == before
    assert run(v, c, job, svc, mode_="on").code == ap.C_WRITTEN            # control: open walls let it through


def test_the_ledger_is_asked_strictly_about_the_quote_the_turn_and_the_new_text(svc, monkeypatch):
    import memory_forgotten
    import turn_forget

    seen = []

    async def matches(user_id, text, *, near=False, strict=False):
        seen.append((text, near, strict))
        raise memory_forgotten.LedgerUnavailable("down")

    monkeypatch.setattr(memory_forgotten, "matches", matches)
    v, c, job, _rid = setup(svc)
    assert run(v, c, job, svc, mode_="on", walls=ap.Walls()).code == ap.C_LEDGER
    assert seen == [(SAID, True, True)]
    w = Walls()
    run(v, c, job, svc, mode_="shadow", walls=w)
    assert w.asked == [SAID, SAID, "User's daughter Juno is 8 years old."]
    # "forget that" retracted the turn: the real in-process wall
    turn_forget.reset()
    turn_forget.note_user_turn(UID, SAID)
    turn_forget.retract(UID, turn_forget.previous_turn(UID))
    assert ap.Walls().retracted(UID, SAID) is True
    turn_forget.reset()


def test_a_member_erase_since_the_session_was_picked_drops_the_write(svc):
    v, c, job, _rid = setup(svc)

    async def erase():
        async with user_erase_gate.erasing(UID):
            pass

    asyncio.run(erase())
    before = dict(svc._col.rows)
    assert run(v, c, job, svc, mode_="on").code == ap.C_ERASED and svc._col.rows == before
    fresh = replace(job, generation=user_erase_gate.generation(UID))
    assert run(v, c, fresh, svc, mode_="on").code == ap.C_WRITTEN          # control: scheduled after the erase


async def _no_walls(*_a, **_k):
    return None


async def _no_authority(job, turn, plans, svc):
    return {p.row_id: 0 for p in plans}


@pytest.mark.parametrize("guard,stub,arrange", [
    ("_forget_walls", _no_walls, "forgotten"),
    ("_authority", _no_authority, "confirmed"),
])
def test_negative_control_without_the_guard_the_refused_write_happens(svc, monkeypatch, guard, stub, arrange):
    """Each wall is what stops its case: with the wall removed, the very write it refused goes through ``review(edit)``."""
    v, c, job, rid = setup(svc)
    walls = Walls(forgotten=True) if arrange == "forgotten" else Walls()
    if arrange == "confirmed":
        text, meta = svc._col.rows[rid]
        svc._col.rows[rid] = (text, {**meta, "authority_class": auth.USER_CONFIRMED, "authority": auth.USER_CONFIRMED})
    assert run(v, c, job, svc, mode_="on", walls=walls).code in (ap.C_FORGOTTEN, ap.C_AUTHORITY)
    monkeypatch.setattr(ap, guard, stub)
    assert run(v, c, job, svc, mode_="on", walls=walls).code == ap.C_WRITTEN
    assert approved(svc) == ["User's daughter Juno is 8 years old."]


# -- the text: never less, all rows or none, never a stale row ------------------------------------------------------

def test_a_text_that_cannot_be_built_or_would_lose_a_value_is_refused(svc, monkeypatch):
    v, c, job, _rid = setup(svc)
    before = dict(svc._col.rows)
    monkeypatch.setattr(render, "splice", lambda *a, **k: None)
    assert run(v, c, job, svc, mode_="on").code == ap.C_RENDER
    monkeypatch.setattr(render, "splice", lambda *a, **k: ("User's daughter Juno is older now.", "24:33"))
    assert run(v, c, job, svc, mode_="on").code == ap.C_LESS and svc._col.rows == before


def test_all_the_rows_of_a_claim_move_or_none_does(svc):
    v, c, job, rid = setup(svc)
    twin = replace(v.edits[0], row_id="row-nobody-read")
    both = replace(v, edits=(v.edits[0], twin))
    before = dict(svc._col.rows)
    assert run(both, c, job, svc, mode_="on").code == ap.C_STALE_ROW and svc._col.rows == before
    assert rid in before


def test_a_row_that_changed_since_it_was_read_is_left_alone(svc):
    v, c, job, rid = setup(svc)
    text, meta = svc._col.rows[rid]
    svc._col.rows[rid] = ("User's daughter Juno is 9 years old.", meta)
    assert run(v, c, job, svc, mode_="on").code == ap.C_STALE_ROW
    svc._col.rows[rid] = (text, {**meta, "status": "archived"})
    assert run(v, c, job, svc, mode_="on").code == ap.C_STALE_ROW


RENDER_CASES = [
    ("age", "Juno is seven years old", "seven years old", "seven", "eight", "Juno is eight years old"),
    ("event_date", "Book club is on the 12th of December 2026.", "12th of December 2026", None, "the 19th",
     "Book club is on the 19th of December 2026."),
    ("event_date", "The dentist is on the twentieth of November.", "twentieth of November", "the 20th", "the 27th of november",
     "The dentist is on the 27th of November."),
    ("event_date", "Book club is on the 12th of December.", "12th of December", None, "the 12th of january",
     "Book club is on the 12th of January."),
    ("amount", "User has saved twelve thousand dollars for the boat fund.", "twelve thousand dollars", "12k", "13.5k",
     "User has saved 13.5k dollars for the boat fund."),
    ("amount", "User's savings are $12,000.", "$12,000", "$12,000", "$13,500", "User's savings are $13,500."),
    ("time_of_day", "Swimming is at 4pm.", "4pm", "4", "5", "Swimming is at 5pm."),
    ("schedule", "Juno's swimming is on Thursday.", "Thursday", "thursday", "wednesday", "Juno's swimming is on Wednesday."),
    ("location", "User's sister Wynne lives in Bendigo.", "Bendigo", "Bendigo", "Ballarat", "User's sister Wynne lives in Ballarat."),
]


@pytest.mark.parametrize("attribute,row,piece,old,new,want", RENDER_CASES, ids=[c[5][:28] for c in RENDER_CASES])
def test_the_new_sentence_keeps_the_row_and_reads_back(attribute, row, piece, old, new, want):
    ref = {"kind": "label", "key": "x"}
    a = attr(row, ref, attribute, piece)
    ctx = vm.Context(UID, [vm.Turn(0, AT, "x")], [vm.Row("r0", row, attrs=(a,))], vm.Graph([]))
    claim = mc.parse_claim({"type": "correction", "turn": 0, "quote": "x", "subject": "x", "attribute": attribute, "old": old,
                            "new": new, "time": "current"})
    ok, n_old, n_new = mc.normalise_pair(attribute, old, new)
    assert ok
    planned = vm._new_value(attribute, n_new, a.value_norm, AT)
    text, span = render.splice(attribute, row, a.span, a.value_norm, planned, a.unit, claim.new)
    assert text == want and mc.same_value(attribute, mc.span_value(attribute, text, span).value, planned)
    i = row.index(piece)
    assert text[:i] == row[:i] and text.endswith(row[i + len(piece):])       # nothing outside the span moved


def test_splice_refuses_what_it_cannot_read_back():
    row = "Book club is on the 12th of December."
    a = attr(row, {"kind": "label", "key": "x"}, "event_date", "12th of December")
    assert render.splice("event_date", row, a.span, a.value_norm, {"m": 12, "d": 19}, "date", "soon") is None
    assert render.splice("event_date", row, "0:4", a.value_norm, {"m": 12, "d": 19}, "date", "the 19th") is None   # not the value
    assert render.splice("event_date", row, "5:999", a.value_norm, {"m": 12, "d": 19}, "date", "the 19th") is None
    assert render.shift_span("0:4", 20, 36, 3) == "0:4" and render.shift_span("40:44", 20, 36, 3) == "43:47"
    assert render.shift_span("30:44", 20, 36, 3) is None


# -- a health event -------------------------------------------------------------------------------------------------

def event_setup(svc, said, subject, event, graph=GRAPH):
    turns = [vm.Turn(0, AT, said, people_graph.content_turn_id(UID, said))]
    ctx = vm.Context(UID, turns, [], graph, detector=medical_safety.detect)
    parsed = mc.parse_output({"claims": [{"type": "emergency", "turn": 0, "quote": said, "subject": subject, "event": event,
                                          "time": "current"}], "abstain": None})
    return vm.validate(parsed, ctx)[0], parsed.items[0], ap.Job(UID, turns, [], people={"p1": ("Juno", "daughter")})


def test_a_health_event_is_filed_only_for_a_caller_that_names_the_class(svc):
    v, c, job = event_setup(svc, "i fell over in the kitchen and i cant get up", "I", "fall")
    assert v.verdict == "apply"
    assert run(v, c, job, svc, mode_="on").code == ap.C_WOULD and svc._col.rows == {}      # emergency flips last
    out = run(v, c, job, svc, mode_="on", classes=("correction", "emergency"))
    assert out.code == ap.C_WRITTEN
    text, meta = svc._col.rows[out.new_ids[0]]
    assert text == "User had a fall on 14 November 2026 (a medical emergency)." and meta["memory_type"] == "event"
    assert auth.row_rank(meta, text) >= auth.DERIVED_RANK


def test_another_persons_event_names_them_from_the_graph_and_a_past_one_is_not_filed(svc):
    said = "my daughter juno has crushing chest pain and cant breathe"
    v, c, job = event_setup(svc, said, "Juno", "heart")
    assert ap.event_text(v, job) == "User's daughter Juno had chest pain on 14 November 2026 (a medical emergency)."
    turns = [vm.Turn(0, AT, "i had crushing chest pain in 2020", "ut-0")]
    past = mc.parse_output({"claims": [{"type": "emergency", "turn": 0, "quote": "i had crushing chest pain in 2020",
                                        "subject": "I", "event": "heart", "time": "past"}], "abstain": None})
    pv = vm.validate(past, vm.Context(UID, turns, [], GRAPH, detector=medical_safety.detect))[0]
    assert pv.verdict == "abstain"
    assert run(pv, past.items[0], ap.Job(UID, turns, []), svc, mode_="on", classes=("emergency",)).code == ap.C_NOT_VALIDATED
    assert svc._col.rows == {}


# -- the registry seam, the old-path comparison, the logs -----------------------------------------------------------

def test_the_registry_adapter_records_people_and_self_and_says_when_it_is_absent(monkeypatch):
    v = vm.Registered
    entries = [v(JUNO, "age", "superseded", {"v": 7.0}, AT, "years", "c" * 16, "ut-1", ("r0",)),
               v(JUNO, "age", "asserted", {"v": 8.0}, AT, "years", "c" * 16, "ut-1", ("r0",)),
               v({"kind": "label", "key": "book club"}, "event_date", "asserted", {"d": 19}, AT, "date", "d" * 16)]
    monkeypatch.setitem(sys.modules, "memory_value_registry", None)             # not importable on this branch
    ap.STATS.clear()
    assert asyncio.run(ap.ValueRegistry().record(UID, entries, generation=0, row_ids={})) == 0
    assert ap.STATS["registry_absent"] == 1
    calls = []

    async def record(user_id, ref, attribute, **kw):
        calls.append((user_id, ref, attribute, kw))
        return 2

    fake = types.SimpleNamespace(SELF="self", ATTRIBUTES=("age", "event_date"), record=record)
    monkeypatch.setitem(sys.modules, "memory_value_registry", fake)
    assert asyncio.run(ap.ValueRegistry().record(UID, entries, generation=3, row_ids={"r0": "r9"})) == 2
    (user, ref, attribute, kw), = calls
    assert (user, ref, attribute) == (UID, "p1", "age") and kw["superseded"] == [{"v": 7.0}] and kw["asserted"] == {"v": 8.0}
    assert kw["row_ids"] == ["r0", "r0", "r9", "r9"] and kw["scheduled_at"] == 3 and ap.STATS["registry_unrecordable"] == 1


def test_the_old_path_comparison(svc):
    v, c, job, rid = setup(svc)
    out = run(v, c, job, svc, mode_="shadow")
    old = ap.old_path_plan(SAID, job.rows)
    assert old == {rid: "User's daughter Juno is 8 years old."}
    assert ap.compare(old, {p.row_id: p.new_text for p in out.plans}) == "agree"
    assert ap.compare({}, {}) == "abstain" and ap.compare({}, {"r": "a"}) == "claim_only"
    assert ap.compare({"r": "a"}, {}) == "old_only" and ap.compare({"r": "a"}, {"r": "b"}) == "disagree"
    year = "User's book club is on the 12th of December 2026."
    assert ap.old_path_plan("book club moved to the 19th", [vm.Row("r0", year)]) == {}      # the shape the regex misses
    assert ap.old_path_plan("hello there", job.rows) == {}


def test_logs_carry_codes_and_counts_only(svc, caplog):
    v, c, job, _rid = setup(svc)
    with caplog.at_level(logging.DEBUG, logger="memory_claims_apply"):
        run(v, c, job, svc, mode_="shadow")
        run(v, c, job, svc, mode_="on", walls=Walls(forgotten=True))
        run(v, c, job, svc, mode_="on")
        ap.log_shadow("correction", "idle", "agree", None, 1, 1, 12, "brain")
    lines = [r.getMessage() for r in caplog.records if r.name == "memory_claims_apply"]
    assert [ln.split(" code=")[1].split()[0] for ln in lines if ln.startswith("MEMORY_CLAIM_APPLY")] == [
        "would_write", "forgotten", "written"]
    assert lines[-1] == "MEMORY_CLAIM_SHADOW class=correction lane=idle verdict=agree rule=- rows_old=1 rows_claim=1 ms=12 model=brain"
    blob = " ".join(lines).lower()
    assert not any(w in blob for w in ("juno", "daughter", "years", UID, " 7", " 8 "))
