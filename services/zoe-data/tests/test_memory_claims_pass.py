"""The idle claim pass (memory redesign step 7, ``memory_claims_pass``): shadow writes nothing and logs one counts-only
agreement line per flagged turn, the bounds, the scheduler, the blind attribute read, the forget walls, and how the brain
call is made. A scripted reader stands in for the model; the store is a real ``MemoryService`` on an in-memory collection.
Synthetic people, fixed dates. ``ci_safe``."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import types

import pytest

import brain_scheduler
import brain_slot_guard
import memory_claims as mc
import memory_claims_apply as ap
import memory_claims_pass as cp
import memory_idle_consolidation as idle
import memory_service
import people_graph
import turn_forget
import user_erase_gate
import user_prefs
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "demo_mc_p"
AT = people_graph.parse_ts("2026-11-14T09:00:00+00:00")
ROW, SAID = "User's daughter Juno is 7 years old.", "juno is 8 not 7"
CLAIM = {"type": "correction", "turn": 0, "quote": SAID, "subject": "Juno", "attribute": "age", "old": "7", "new": "8",
         "time": "current"}
ATTR = {"row": 0, "subject": "Juno", "attribute": "age", "span": "7 years old"}


def out(*claims):
    return json.dumps({"claims": list(claims), "abstain": None if claims else "no_correction"})


class Reader:
    def __init__(self, claims=None, rows=None, fail=False):
        self.c, self.r, self.fail, self.calls = claims, rows, fail, []

    async def claims(self, turns, flagged):
        self.calls.append(("claims", tuple(flagged)))
        if self.fail:
            raise OSError("brain down")
        return self.c if self.c is not None else out(CLAIM)

    async def attributes(self, texts):
        self.calls.append(("rows", len(texts)))
        return json.dumps({"rows": self.r if self.r is not None else [ATTR]})


class Src:
    def __init__(self, svc, sessions, people=(("p1", "Juno", "daughter"),), marked=()):
        self.svc, self.s, self.people, self.m, self.cleared = svc, sessions, people, frozenset(marked), []

    async def sessions(self):
        return [{"session_id": k, "user_id": UID} for k in self.s]

    async def turns(self, session_id):
        return [{"id": f"{session_id}-{i}", "role": "user", "content": t, "at": AT, "metadata": json.dumps({"user_id": UID, **m})}
                for i, (t, m) in enumerate((x if isinstance(x, tuple) else (x, {})) for x in self.s[session_id])]

    async def graph(self, user_id):
        return cp.graph_of(self.people)

    async def rows(self, user_id):
        return await self.svc.list_all_by_status_strict(user_id=user_id, status="approved")

    def marked(self, user_id):
        return self.m

    def clear_marker(self, user_id, message_id):
        self.cleared.append(message_id)


class Walls:
    def retracted(self, _u, _t):
        return False

    async def forgotten(self, _u, _t):
        return False


@pytest.fixture
def svc(monkeypatch):
    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_MEMORY_CLAIMS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_BRAIN_SCHEDULER", "off")
    s = MemoryService(data_dir="/nonexistent/zoe-test-memory-claims-pass")
    s._col = _Col()
    s._collection = lambda: s._col

    async def no(*_a, **_k):
        return None

    async def not_opted_out(_uid):
        return False

    s._append_audit = no
    monkeypatch.setattr(memory_service, "_user_opted_out", not_opted_out)
    monkeypatch.setattr(user_prefs, "is_memory_opted_out", not_opted_out)
    user_erase_gate.reset()
    turn_forget.reset()
    cp.reset_state()
    return s


def seed(svc, text=ROW):
    return asyncio.run(svc.ingest(text, user_id=UID, source="voice_fact", confidence=0.9)).id


def sweep(svc, src, reader, mode="shadow"):
    return asyncio.run(cp.run_sweep(src=src, reader=reader, svc=svc, walls=Walls(), mode_=mode))


def shadow_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("MEMORY_CLAIM_SHADOW")]


# -- off, shadow, on -----------------------------------------------------------------------------------------------

def test_off_does_no_work_at_all(svc):
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("the pass touched a source while off")

    assert asyncio.run(cp.run_sweep(src=Boom(), reader=Boom())) == {"mode": "off"}


def test_shadow_logs_agreement_writes_nothing_and_reads_a_turn_once(svc, caplog):
    seed(svc)
    before = {k: (d, dict(m)) for k, (d, m) in svc._col.rows.items()}
    src, reader = Src(svc, {"s1": ["hello zoe", SAID]}), Reader(out({**CLAIM, "turn": 1}))
    with caplog.at_level(logging.INFO):
        res = sweep(svc, src, reader)
    assert (res["sessions"], res["flagged"], res["claims"], res["written"]) == (1, 1, 1, 0)
    assert svc._col.rows == before and src.cleared == []
    (line,) = shadow_lines(caplog)
    assert "class=correction lane=idle verdict=agree rule=- rows_old=1 rows_claim=1" in line and line.endswith("model=brain")
    assert reader.calls == [("claims", (1,)), ("rows", 1)]
    blob = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("memory_claims")).lower()
    assert not any(w in blob for w in ("juno", "daughter", "years old", UID))
    sweep(svc, src, reader)                                   # the turn was handled: no second model call
    assert len(reader.calls) == 2


def test_shadow_reports_a_shape_only_the_claim_path_reads(svc, caplog):
    seed(svc, "User's book club is on the 12th of December 2026.")
    said = "book club moved to the 19th"
    claim = {**CLAIM, "quote": said, "subject": "book club", "attribute": "event_date", "old": None, "new": "the 19th"}
    reader = Reader(out(claim), [{"row": 0, "subject": "book club", "attribute": "event_date", "span": "12th of December 2026"}])
    with caplog.at_level(logging.INFO):
        sweep(svc, Src(svc, {"s1": [said]}, people=(), marked=["s1-0"]), reader)
    assert "verdict=claim_only rule=- rows_old=0 rows_claim=1" in shadow_lines(caplog)[0]


def test_on_applies_the_correction_and_clears_the_marker(svc):
    rid = seed(svc)
    src = Src(svc, {"s1": [SAID]})
    res = sweep(svc, src, Reader(), mode="on")
    assert res["written"] == 1 and src.cleared == ["s1-0"]
    assert svc._col.rows[rid][1]["status"] == "superseded"
    assert sorted(d for d, m in svc._col.rows.values() if m["status"] == "approved") == ["User's daughter Juno is 8 years old."]


def test_a_session_with_no_flagged_turn_costs_no_model_call(svc):
    reader = Reader()
    res = sweep(svc, Src(svc, {"s1": ["good morning", "what is the weather"]}), reader)
    assert res["sessions"] == 0 and reader.calls == []


# -- bounds --------------------------------------------------------------------------------------------------------

def test_the_pass_is_bounded_per_sweep_per_hour_and_by_a_breaker(svc, monkeypatch):
    many = {f"s{i}": [f"juno is {i + 9} not {i + 8}"] for i in range(6)}
    reader = Reader(out())
    res = sweep(svc, Src(svc, many), reader)
    assert res["flagged"] == cp.MAX_TURNS_PER_PASS and len(reader.calls) == cp.MAX_TURNS_PER_PASS
    cp.reset_state()
    monkeypatch.setattr(cp, "MAX_PER_HOUR", 1)
    reader = Reader(out())
    sweep(svc, Src(svc, many), reader)
    assert len(reader.calls) == 1 and cp.STATS["skipped_budget"] == 2
    cp.reset_state()
    monkeypatch.setattr(cp, "MAX_PER_HOUR", 30)
    failing = Reader(fail=True)
    sweep(svc, Src(svc, many), failing)
    assert len(failing.calls) == cp.BREAKER_AFTER
    again = Reader(out())
    sweep(svc, Src(svc, many), again)                         # breaker open: nothing is asked, the turns stay unhandled
    assert again.calls == [] and not cp._handled


def test_a_call_the_scheduler_would_not_allow_is_not_made(svc, monkeypatch):
    monkeypatch.setenv("ZOE_BRAIN_SCHEDULER", "log")
    assert brain_scheduler.DECLARED[cp.LABEL][0] == brain_scheduler.POST_TURN
    brain_slot_guard.reset()                                  # no household turn in this process: a post-turn pass is idle
    monkeypatch.setattr(brain_scheduler, "is_day", lambda now=None: True)
    idle_before = brain_scheduler.STATS["idle_4b_calls"]
    reader = Reader()
    res = sweep(svc, Src(svc, {"s1": [SAID]}), reader)
    assert reader.calls == [] and res.get("flagged") == 1 and not cp._handled
    assert cp.STATS["skipped_scheduler"] == 1 and brain_scheduler.STATS["idle_4b_calls"] == idle_before
    brain_slot_guard.note_turn_completed()                    # control: a household turn just ended -> allowed
    sweep(svc, Src(svc, {"s1": [SAID]}), reader)
    assert reader.calls and reader.calls[0][0] == "claims"
    brain_slot_guard.reset()


# -- the blind attribute read is checked by code -------------------------------------------------------------------

@pytest.mark.parametrize("item", [
    {**ATTR, "span": "8 years old"},                          # not in the row
    {**ATTR, "span": "years old"},                            # no value
    {**ATTR, "attribute": "event_date"},                      # the span is no date
    {**ATTR, "subject": "she"},                               # a pronoun resolves nothing
    {**ATTR, "row": 4},                                       # no such row
])
def test_an_attribute_read_code_cannot_check_is_dropped(svc, caplog, item):
    seed(svc)
    before = dict(svc._col.rows)
    with caplog.at_level(logging.INFO):
        sweep(svc, Src(svc, {"s1": [SAID]}), Reader(rows=[item]), mode="on")
    assert svc._col.rows == before
    assert "verdict=old_only rule=9 rows_old=1 rows_claim=0" in shadow_lines(caplog)[0]


def test_read_attrs_needs_a_single_verbatim_span_and_a_subject_in_the_row():
    graph = cp.graph_of([("p1", "Juno", "daughter"), ("p2", "Ned", "dad")])
    assert graph.related("father") == ("p2",) and graph.related("daughter") == ("p1",)
    raw = json.dumps({"rows": [ATTR, {"row": 1, "subject": "Juno", "attribute": "age", "span": "7"},
                               {"row": 2, "subject": "Wren", "attribute": "age", "span": "7"}]})
    got = cp.read_attrs(raw, [ROW, "Juno is 7 and has 7 fish", "Juno is 7"], UID, graph, AT)
    assert list(got) == [0] and got[0][0].span == "24:35" and got[0][0].subject_ref == {"kind": "person", "id": "p1"}
    assert cp.read_attrs("not json", [ROW], UID, graph, AT) == {}


# -- forget --------------------------------------------------------------------------------------------------------

def test_an_unreadable_ledger_skips_the_session_before_any_model_call(svc, monkeypatch):
    import memory_digest

    async def down(user_id, turns, reader):
        raise OSError("ledger down")

    monkeypatch.setattr(memory_digest, "_skip_forgotten_turns", down)
    reader = Reader()
    res = sweep(svc, Src(svc, {"s1": [SAID]}), reader)
    assert reader.calls == [] and res["sessions"] == 0 and not cp._handled


def test_a_retracted_or_rejected_turn_changes_nothing(svc, caplog):
    seed(svc)
    before = dict(svc._col.rows)
    turn_forget.note_user_turn(UID, SAID)
    turn_forget.retract(UID, turn_forget.previous_turn(UID))
    with caplog.at_level(logging.INFO):
        sweep(svc, Src(svc, {"s1": [SAID]}), Reader(), mode="on")
    assert svc._col.rows == before and "rule=18" in shadow_lines(caplog)[0]
    turn_forget.reset()
    cp.reset_state()
    reader = Reader()
    sweep(svc, Src(svc, {"s1": [(SAID, {"speaker_verified": False})]}), reader, mode="on")
    assert reader.calls == [] and svc._col.rows == before      # a speaker the gate rejected is never read as the member


# -- how the brain is called ---------------------------------------------------------------------------------------

def test_the_brain_call_goes_through_the_slot_gate_under_its_label_with_a_strict_schema(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            seen["url"], seen["payload"] = url, json
            return types.SimpleNamespace(raise_for_status=lambda: None,
                                         json=lambda: {"choices": [{"message": {"content": out()}}]})

    async def gate(label, call):
        seen["label"] = label
        return await call()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=Client))
    monkeypatch.setattr(brain_slot_guard, "run_background", gate)
    turns = [cp.vm.Turn(0, AT, SAID)]
    assert asyncio.run(cp.BrainReader().claims(turns, [0])) == out()
    p = seen["payload"]
    assert seen["label"] == cp.LABEL and seen["url"].endswith("/v1/chat/completions")
    assert "id_slot" not in p and "session_id" not in p and p["temperature"] == 0 and p["max_tokens"] == cp.MAX_TOKENS
    fmt = p["response_format"]["json_schema"]
    assert fmt["strict"] is True and fmt["schema"]["properties"]["claims"]["maxItems"] == mc.MAX_CLAIMS
    kinds = fmt["schema"]["properties"]["claims"]["items"]["oneOf"]
    assert [k["properties"]["type"]["const"] for k in kinds] == list(mc.CLAIM_TYPES)
    assert kinds[0]["properties"]["attribute"]["enum"] == list(mc.ATTRIBUTES) and SAID in p["messages"][1]["content"]


def test_the_idle_loop_tick_is_dark_when_the_flag_is_off(monkeypatch):
    called = []

    async def run_sweep():
        called.append(1)
        return {}

    monkeypatch.setitem(sys.modules, "memory_claims_pass", types.SimpleNamespace(run_sweep=run_sweep))
    monkeypatch.delenv("ZOE_MEMORY_CLAIMS", raising=False)
    asyncio.run(idle._claims_tick())
    assert called == []
    monkeypatch.setenv("ZOE_MEMORY_CLAIMS", "shadow")
    asyncio.run(idle._claims_tick())
    assert called == [1]
