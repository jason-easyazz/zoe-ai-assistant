"""The observation gate: a model's nightly reading of the owner is stored only when the owner's words carry it.

ZMB reflection axis, measured on main 2026-10-07 (K1 5 of 10 observations TRUE, K5 failing): the nightly digest stored
EVERY model proposal as an approved row, so three kinds of mistake were served - a link nobody stated ("Dagny is
Jarvis's husband"), a hedged restatement of something the owner said plainly ("Jarvis probably lives in Pellham") and a
"You told me ..." the owner never said. The inputs below are the bench's own (``scripts/perf/zmb/life.py``, the baseline
seed), played through the REAL ``run_memory_digest`` (only the model calls are scripted), the REAL ``MemoryService.ingest``
authority wall and the REAL ``memory_authority`` helpers. Synthetic names, no network, no model, no live store (``ci_safe``).

RED-BEFORE-GREEN: every claim has its control - with ``ZOE_DIGEST_OBSERVATION_GATE=off`` the fabricated link, the guess and
the invention are stored approved again (the measured defect), and the same bench cells go red (``observation_gate`` control).
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

import memory_authority as ma
import memory_service
from memory_service import MemoryService
from test_memory_authority import _Col

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "perf"))
from zmb import life as lifemod  # noqa: E402
from zmb import scorers_cap as cap  # noqa: E402

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_00000001"
SEED = "zmb-v1"
LF = lifemod.life(SEED)
GOLD = lifemod.gold_for_scoring(LF)
TYPED = "\n".join(t["text"] for t in LF.turns if t["speaker"] == "typed")


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    monkeypatch.delenv(ma.OBSERVATION_GATE_ENV, raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-observation-gate")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    s._col = col
    return s


def _teach(svc, text):
    return asyncio.run(svc.ingest(text, user_id=UID, source="voice_fact", confidence=0.9))


def _night(svc, monkeypatch, proposes, *, transcript=TYPED, extra_items=None):
    """The real nightly digest over ``transcript`` with the model's output scripted to ``proposes``."""
    import memory_digest as md

    async def todays(*_a, **_k):
        return transcript

    async def extract(_text):
        return [{"fact": f, "type": "profile"} for f in proposes] + list(extra_items or [])

    async def no(*_a, **_k):
        return False

    async def none(*_a, **_k):
        return 0

    async def no_blob(*_a, **_k):
        return ""
    stub = types.ModuleType("zoe_agent")
    stub._mempalace_load_user_facts = no_blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)
    monkeypatch.setattr(md, "_load_todays_messages", todays)
    monkeypatch.setattr(md, "_extract_facts_with_gemma", extract)
    monkeypatch.setattr(md, "_is_contradiction", no)
    monkeypatch.setattr(md, "_emotional_memory_pass", none)
    return asyncio.run(md.run_memory_digest(UID))


def _by_status(svc, status):
    return [d for d, m in svc._col.rows.values() if m.get("status") == status and m.get("origin") == "digest"]


def _all_proposals():
    return (LF.proposals_true + LF.proposals_fabricated + LF.proposals_stale + LF.proposals_hedged + LF.proposals_presented_as_said)


def _seed_user_rows(svc):
    for t in LF.turns:
        if t["speaker"] == "taught":
            _teach(svc, t["text"])


# ── the pure judge ───────────────────────────────────────────────────────────

def test_a_fabricated_link_is_unsupported_a_paraphrase_the_owner_put_in_one_sentence_is_supported():
    for fact in LF.proposals_fabricated:
        v = ma.check_observation(fact, TYPED)
        assert v.kind == "unsupported" and "no_support" in v.reasons, fact
    for fact in LF.proposals_true:
        v = ma.check_observation(fact, TYPED)
        assert v.kind == "supported" and v.basis in ("anchored_user_turn", ma.COSTATED_BASIS), (fact, v)
    paraphrase = LF.proposals_true[0]                      # "X accepted the offer from Y and starts there soon" from "X got the offer from Y!"
    assert ma.supports(paraphrase, TYPED) is False          # the existing entailment helper does not carry it ...
    assert ma.check_observation(paraphrase, TYPED).basis == ma.COSTATED_BASIS     # ... one sentence naming everything it names does


def test_a_relation_the_owner_never_stated_is_not_carried_by_two_names_in_one_sentence():
    said = "Dana and Leo came to dinner on Friday."
    assert ma.check_observation("Dana is Leo's husband.", said).kind == "unsupported"              # the sentence has no relation word
    assert ma.check_observation("Dana and Leo came to dinner.", said).kind == "supported"
    assert ma.check_observation("Dana quit Leo.", "Dana started with Leo on Monday.").kind == "unsupported"   # polarity / tense must agree


def test_you_told_me_from_a_model_is_removed_or_reworded_as_an_inference():
    said_by_owner = LF.proposals_true[1]                                       # supported by the owner's words
    v = ma.check_observation("You told me " + said_by_owner[0].lower() + said_by_owner[1:], TYPED)
    assert v.kind == "supported" and "attributed" in v.reasons and not v.text.lower().startswith("you ")
    for fact in LF.proposals_presented_as_said:
        v = ma.check_observation(fact, TYPED)
        assert v.kind == "unsupported" and "attributed" in v.reasons
        assert v.text.startswith("Possibly ") and "you told me" not in v.text.lower() and "you said" not in v.text.lower()


def test_a_hedged_restatement_of_what_the_owner_said_plainly_is_a_restatement_not_a_new_fact():
    for fact in LF.proposals_hedged:
        v = ma.check_observation(fact, TYPED)
        assert v.kind == "restatement" and "hedged" in v.reasons, fact
    # a guess about something nobody said is just unsupported
    assert ma.check_observation("Dagny probably lives in Zorbleton.", TYPED).kind == "unsupported"


def test_a_quote_that_is_not_verbatim_is_no_evidence_and_a_cited_user_row_is():
    claim = LF.proposals_fabricated[0]
    assert ma.check_observation(claim, None).kind == "unsupported"                       # the model's quote was invented
    assert "quote_not_verbatim" in ma.check_observation(claim, None).reasons
    # the owner's own approved row, cited by id, carries what the transcript does not
    v = ma.check_observation("Brynja is Tamsin's mother.", "nothing relevant here", cited_texts=["Brynja is Tamsin's mother."])
    assert v.kind == "supported" and v.basis == ma.CITED_BASIS


def test_the_gate_modes():
    import os
    assert ma.observation_gate_mode() == "enforce"
    for raw, want in (("off", "off"), ("0", "off"), ("shadow", "shadow"), ("enforce", "enforce"), ("1", "enforce")):
        os.environ[ma.OBSERVATION_GATE_ENV] = raw
        try:
            assert ma.observation_gate_mode() == want
        finally:
            os.environ.pop(ma.OBSERVATION_GATE_ENV, None)


# ── the real nightly digest ──────────────────────────────────────────────────

def test_k1_k5_inputs_through_the_real_digest_store_only_what_the_owners_words_carry(svc, monkeypatch):
    _seed_user_rows(svc)
    out = _night(svc, monkeypatch, _all_proposals())
    approved = _by_status(svc, "approved")
    labels = {t: cap.classify_observation(t, GOLD)[0] for t in approved}
    assert "false" not in labels.values(), labels                                       # K1: no fabricated link, no stale value as current
    assert sum(1 for v in labels.values() if v == "true") >= 3                          # and not an empty layer: K1 needs >= 3 decidable
    assert not any(t.lower().startswith("you ") or "you told me" in t.lower() for t in approved)          # K5: no invention presented as said
    assert not any(" probably " in t or " seems " in t for t in approved)               # no guess about a thing said plainly
    assert out["observations_restated"] == len(LF.proposals_hedged)
    for fact in LF.proposals_fabricated:
        assert fact not in approved and fact in _by_status(svc, "pending")           # held (never served), not lost
    assert out["observations_held"] >= len(LF.proposals_fabricated) + len(LF.proposals_presented_as_said)


def test_control_with_the_gate_off_the_measured_defect_returns(svc, monkeypatch):
    monkeypatch.setenv(ma.OBSERVATION_GATE_ENV, "off")
    _seed_user_rows(svc)
    _night(svc, monkeypatch, _all_proposals())
    approved = _by_status(svc, "approved")
    assert all(f in approved for f in LF.proposals_fabricated)                         # a fabricated link is served again
    assert any("you told me" in t.lower() for t in approved)                           # and an invention presented as said
    assert any(" probably " in t for t in approved)                                    # and a guess about something said plainly
    assert "false" in {cap.classify_observation(t, GOLD)[0] for t in approved}


def test_shadow_mode_logs_what_it_would_hold_and_changes_nothing(svc, monkeypatch, caplog):
    import logging
    caplog.set_level(logging.INFO)
    monkeypatch.setenv(ma.OBSERVATION_GATE_ENV, "shadow")
    _night(svc, monkeypatch, LF.proposals_fabricated)
    assert all(f in _by_status(svc, "approved") for f in LF.proposals_fabricated)
    gate_lines = [r.getMessage() for r in caplog.records if "observation gate" in r.getMessage()]
    assert gate_lines and all("WOULD_HOLD" in ln for ln in gate_lines)
    assert not any(n in ln for ln in gate_lines for n in ("Brynja", "Dagny", "Zora", "Tamsin"))      # labels only: never the fact


def test_a_held_observation_that_disputes_something_the_owner_said_is_still_the_disputed_candidate(svc, monkeypatch):
    _seed_user_rows(svc)
    sis_new = f"{LF.stale[0][0].title()} lives in {LF.stale[0][2].title()}."
    stale = LF.proposals_stale[0]
    out = _night(svc, monkeypatch, [stale])
    assert stale not in _by_status(svc, "approved")
    # either held pending (nobody stated it) or disputed against the owner's row (the authority wall): never approved, never lost
    assert stale in _by_status(svc, "pending") + _by_status(svc, "disputed")
    assert sis_new is not None and out["superseded"] == 0


def test_the_cited_rows_of_an_item_carry_it_and_another_owners_rows_do_not(svc, monkeypatch):
    mine = _teach(svc, "Brynja is Tamsin's mother.")
    claim = "Brynja is Tamsin's mother and lives in Marlowby."
    other = asyncio.run(svc.ingest("Brynja is Tamsin's mother.", user_id="demo_bar_00000002", source="voice_fact", confidence=0.9))
    out = _night(svc, monkeypatch, [], extra_items=[{"fact": "Brynja is Tamsin's mother.", "type": "profile", "source_memory_ids": [mine.id]}],
                 transcript="nothing relevant " * 12)
    assert out.get("observations_held", 0) == 0                                         # supported by the owner's cited row (it is also a duplicate: skipped or kept, never held)
    held = _night(svc, monkeypatch, [], extra_items=[{"fact": claim, "type": "profile", "source_memory_ids": [other.id]}],
                  transcript="nothing relevant " * 12)
    assert held.get("observations_held") == 1                                           # another user's row supports nothing
    unknown = _night(svc, monkeypatch, [], extra_items=[{"fact": claim, "type": "profile", "source_memory_ids": ["no-such-row"]}],
                     transcript="nothing relevant " * 12)
    assert unknown.get("observations_held") == 1


def test_a_cited_model_row_is_not_a_user_statement(svc, monkeypatch):
    model_row = asyncio.run(svc.ingest("Brynja is Tamsin's mother.", user_id=UID, source="digest", confidence=0.8))
    out = _night(svc, monkeypatch, [], extra_items=[{"fact": "Brynja is Tamsin's mother and lives in Marlowby.", "type": "profile",
                                                     "source_memory_ids": [model_row.id]}], transcript="nothing relevant " * 12)
    assert out.get("observations_held") == 1                                            # a model citing a model is not support


def test_a_co_stated_paraphrase_is_stamped_derived_not_user_stated(svc, monkeypatch):
    _night(svc, monkeypatch, [LF.proposals_true[0]])
    (row,) = [m for d, m in svc._col.rows.values() if d == LF.proposals_true[0]]
    assert row["authority_class"] == ma.USER_STATED_DERIVED and row["authority_basis"] == ma.COSTATED_BASIS
    assert row["status"] == "approved" and row["origin"] == "digest"
    # and a derived row never overrides a direct statement of the owner's
    assert ma.RANK[ma.USER_STATED_DERIVED] < ma.USER_RANK


def test_the_rejected_reasons_are_on_the_reject_ledger_as_labels_only(svc, monkeypatch):
    import memory_reject_ledger as led
    led.reset_for_tests()
    before = dict(led.summary(24)["reasons"])               # the ledger file is shared by the whole session: count the DELTA
    _night(svc, monkeypatch, LF.proposals_fabricated + LF.proposals_presented_as_said + LF.proposals_hedged)
    after = led.summary(24)["reasons"]

    def delta(k):
        return after.get(k, 0) - before.get(k, 0)
    assert delta("guard_observation_unsupported") >= 1 and delta("guard_observation_attributed_to_user") >= 1
    assert delta("guard_observation_restates_user") == len(LF.proposals_hedged)
    assert not any("Brynja" in k or "Dagny" in k for k in after)
