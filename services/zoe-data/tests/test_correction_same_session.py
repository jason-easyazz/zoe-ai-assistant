"""An explicit "it's actually X, not Y" by the SAME member in the SAME session supersedes the misheard fact - and never crosses
an identity.

Live 2026-10-14 (shape only): "I manage a <misheard> shop" then, twenty seconds later, "It's actually a <right> shop, not a
<misheard> shop." The wrong row was stored anyway, for three reasons this suite pins:

1. the correction tier only read name-shaped values and a single sentence ("a bike shop" has an article; the turn repeats
   itself in a second sentence) - and read "it's actually Perth" as the value "actually Perth";
2. it only fixed rows ALREADY stored, but the misheard turn's digest waits for the brain's one slot and landed minutes AFTER
   the correction (``correct_fact``: the correction is kept for a digest of an earlier turn of the session);
3. (the identity half) both turns were an unrecognised voice: a guest corrects nothing and is corrected by nothing.

Break-the-fix: drop the session filter in ``maybe_fix_recent`` -> ``test_another_sessions_row_is_untouched`` goes red; drop
the ``earlier`` check in ``correct_fact`` -> ``test_a_turn_spoken_after_the_correction_is_not_rewritten`` goes red; key the
registry on session only -> ``test_no_cross_identity_correction`` goes red.

Invented names and facts only; fakes only (ci_safe).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import types
from types import SimpleNamespace

import pytest

import correction_apply as ca
import memory_digest
import memory_service

pytestmark = pytest.mark.ci_safe

MEMBER = "demo_member_wren"
OTHER = "demo_member_ash"
SESSION = "voice-panel-demo-1"
MISHEARD_TURN = "I manage a pike shop and I've got a busy week ahead"
CORRECTION = "It's actually a bike shop, not a pike shop. It's a bike shop."


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("ZOE_CORRECTION_APPLY", "1")
    ca._PENDING.clear()

    async def history(user_id, session_id, current):
        return [MISHEARD_TURN] if (user_id, session_id) == (MEMBER, SESSION) else []

    monkeypatch.setattr(ca, "_recent_user_messages", history)

    async def no_barrier():
        return None

    import provenance_answers
    monkeypatch.setattr(provenance_answers, "_speculation_barrier", no_barrier)
    yield
    ca._PENDING.clear()


# ── the parser ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expect", [
    (CORRECTION, ("bike shop", "pike shop")),
    ("it's actually Perth, not Sydney", ("Perth", "Sydney")),
    ("No, Perth not Sydney", ("Perth", "Sydney")),
    ("I said Perth, not Sydney.", ("Perth", "Sydney")),
    ("Sorry, not a pike shop, a bike shop", ("bike shop", "pike shop")),
    ("you misheard me, the viola not the violin", ("viola", "violin")),
])
def test_correction_pair(text, expect):
    assert ca.correction_pair(text) == expect


@pytest.mark.parametrize("text", [
    "tea, not coffee",                                    # an answer, not a correction (no cue)
    "it's fine, not bad",                                 # an everyday sentence
    "actually it's not sure yet",
    "I manage a pike shop",
    "it's actually a pike shop, not a pike shop",     # nothing changes
])
def test_not_a_correction(text):
    assert ca.correction_pair(text) is None


def test_swap_statement_no_longer_reads_actually_as_part_of_the_value():
    assert ca.swap_statement("it's actually Perth, not Sydney") == ("", "Perth", "Sydney")


# ── the stored row (the digest landed first) ─────────────────────────────────

class FakeSvc:
    def __init__(self, rows):
        self.rows = {r.id: r for r in rows}
        self.edits = []

    async def list_by_status(self, user_id=None, status="approved", limit=0):
        return [r for r in self.rows.values() if r.metadata.get("user_id") == user_id]

    async def get(self, row_id):
        return self.rows.get(row_id)

    async def review(self, row_id, decision="edit", edits=None, **kw):
        self.rows[row_id].text = edits
        self.edits.append((row_id, edits, kw.get("actor"), kw.get("source_excerpt")))
        return self.rows[row_id]


def row(rid, text, user=MEMBER, session=SESSION, age_s=60, excerpt=MISHEARD_TURN):
    """``excerpt``: the member's own words the row was mined from (``source_excerpt``)."""
    return SimpleNamespace(id=rid, text=text, metadata={"user_id": user, "status": "approved", "session_id": session,
                                                         "added_ts": time.time() - age_s, "source_excerpt": excerpt})


def test_the_same_members_row_of_this_session_is_superseded_with_provenance():
    svc = FakeSvc([row("r1", "User manages a pike shop."), row("r2", "User likes the coast.")])
    res = asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=svc))
    assert res is not None and res.kind == "recent"
    assert svc.rows["r1"].text == "User manages a bike shop." and svc.rows["r2"].text == "User likes the coast."
    # review(edit) = supersede (never a delete), the owner's correcting words as the evidence
    assert svc.edits == [("r1", "User manages a bike shop.", ca.SOURCE, " ".join(CORRECTION.split()))]


def test_another_sessions_row_is_untouched():
    svc = FakeSvc([row("r1", "User manages a pike shop.", session="voice-panel-older")])
    assert asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=svc)) is None
    assert svc.edits == []


# ── codex review #2058: a clause is not a value; a row must derive from the corrected value ──────────────────────

FREE_TURN = "I'm free today if you want to catch up about the garden"
CLAUSE_CONTRASTS = [
    "Actually, the meeting is tomorrow, not today",                  # the reviewer's case
    "Actually the meeting's tomorrow, not today",
    "No, I said I'd call her tomorrow, not today",
    "Actually she's coming tomorrow, not today",
    "Sorry, we're meeting tomorrow, not today",
    "Wait, the plumber comes tomorrow, not today",
    "Actually the kids want pizza today, not pasta",
    "No, the dog needs a walk today, not food",
    "Actually, today is busy, not free",
    "I meant the meeting moved, not today",
    "Actually it has to be tomorrow, not today",
    "No, I want to go tomorrow, not today",
    "Actually, tomorrow afternoon, not today",                       # two words against one: not the same shape
    "Sorry, I think tomorrow, not today",
]


@pytest.mark.parametrize("text", CLAUSE_CONTRASTS)
def test_a_clause_shaped_contrast_is_not_a_correction(text):
    assert ca.correction_pair(text) is None


@pytest.mark.parametrize("text", CLAUSE_CONTRASTS)
def test_a_clause_shaped_contrast_changes_no_row(monkeypatch, text):
    async def history(user_id, session_id, current):
        return [FREE_TURN]

    monkeypatch.setattr(ca, "_recent_user_messages", history)
    svc = FakeSvc([row("r1", "User is free today.", excerpt=FREE_TURN)])
    assert asyncio.run(ca.maybe_fix_recent(text, MEMBER, SESSION, svc=svc)) is None
    assert svc.edits == [] and svc.rows["r1"].text == "User is free today."
    assert ca.correct_fact(MEMBER, SESSION, "User is free today.", FREE_TURN) is None


@pytest.mark.parametrize("new,old,ok", [
    ("bike shop", "pike shop", True), ("Perth", "Sydney", True), ("New York", "New Jersey", True), ("tomorrow", "today", True),
    ("meeting is tomorrow", "today", False), ("tomorrow", "Perth", False), ("bike shop", "pike stall", False),
    ("kids want pizza", "pasta", False), ("Tom's", "Sam's", False),
])
def test_same_shape(new, old, ok):
    assert ca.same_shape(new, old) is ok


def test_a_row_from_an_older_turn_is_untouched(monkeypatch):
    """Same member, same session, recent, holds the old value - but it was mined from a turn three back, not one this
    correction follows: a different statement that happens to use the same words."""
    older = "my uncle used to run a pike shop by the river years ago"

    async def history(user_id, session_id, current):
        return [MISHEARD_TURN, "thanks Zoe that is lovely", older]

    monkeypatch.setattr(ca, "_recent_user_messages", history)
    svc = FakeSvc([row("r1", "User's uncle ran a pike shop.", excerpt=older)])
    assert asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=svc)) is None
    assert svc.edits == []
    assert ca.correct_fact(MEMBER, SESSION, "User's uncle ran a pike shop.", older) is None


def test_a_row_whose_own_words_never_held_the_value_is_untouched():
    """The row's text holds the old phrase but the member's words it was mined from do not (a model paraphrase)."""
    svc = FakeSvc([row("r1", "User manages a pike shop.", excerpt="I run the little place on the corner most days")])
    assert asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=svc)) is None
    assert svc.edits == []


def test_an_old_row_is_untouched():
    svc = FakeSvc([row("r1", "User manages a pike shop.", age_s=ca.RECENT_WINDOW_S + 60)])
    assert asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=svc)) is None


def test_no_cross_identity_correction():
    """A member's correction never touches another identity's row - the guest bucket included - and a guest corrects nothing."""
    rows = [row("o1", "User manages a pike shop.", user=OTHER), row("g1", "User manages a pike shop.", user="guest")]
    svc = FakeSvc(rows)
    assert asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=svc)) is None
    for who in ("guest", "voice-guest", "anonymous"):
        assert asyncio.run(ca.maybe_fix_recent(CORRECTION, who, SESSION, svc=svc)) is None
    assert svc.edits == []
    # and the in-process registry holds nothing that another identity could read
    assert ca.correct_fact(OTHER, SESSION, "User manages a pike shop.", MISHEARD_TURN) is None
    assert ca.correct_fact("guest", SESSION, "User manages a pike shop.", MISHEARD_TURN) is None


# ── the deferred digest (the correction landed first) ────────────────────────

def test_a_pending_correction_rewrites_the_later_digest_of_the_earlier_turn():
    asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=FakeSvc([])))
    fixed = ca.correct_fact(MEMBER, SESSION, "User manages a pike shop.", MISHEARD_TURN)
    assert fixed == ("User manages a bike shop.", " ".join(CORRECTION.split()), "pike shop", "bike shop")


def test_a_turn_spoken_after_the_correction_is_not_rewritten():
    asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=FakeSvc([])))
    later = "My cousin runs a pike shop down the road"
    assert ca.correct_fact(MEMBER, SESSION, "User's cousin runs a pike shop.", later) is None


def test_a_short_earlier_turn_does_not_make_a_later_one_look_earlier(monkeypatch):
    async def history(user_id, session_id, current):
        return [MISHEARD_TURN, "ok thanks"]

    monkeypatch.setattr(ca, "_recent_user_messages", history)
    asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=FakeSvc([])))
    later = "ok thanks, and my cousin runs a pike shop down the road"
    assert ca.correct_fact(MEMBER, SESSION, "User's cousin runs a pike shop.", later) is None
    assert ca.correct_fact(MEMBER, SESSION, "User manages a pike shop.", MISHEARD_TURN) is not None


def test_another_session_does_not_inherit_the_correction():
    asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=FakeSvc([])))
    assert ca.correct_fact(MEMBER, "voice-panel-other", "User manages a pike shop.", MISHEARD_TURN) is None


def test_flag_off_registers_nothing():
    import os
    os.environ["ZOE_CORRECTION_APPLY"] = "0"
    try:
        asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=FakeSvc([])))
    finally:
        os.environ["ZOE_CORRECTION_APPLY"] = "1"
    assert ca.correct_fact(MEMBER, SESSION, "User manages a pike shop.", MISHEARD_TURN) is None


def _digest(monkeypatch, user, fact, turn):
    """run_turn_digest with a fake model returning ``fact`` and a fake store; returns (texts, kwargs) ingested."""
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps([{"fact": fact, "type": "profile"}])}}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            return _Resp()

    class _Svc:
        def __init__(self):
            self.ingested = []

        async def ingest(self, text, **kw):
            self.ingested.append((text, kw))
            return None

        async def search(self, *a, **k):
            return []

    stub = types.ModuleType("zoe_agent")

    async def _blob(*a, **k):
        return ""

    stub._mempalace_load_user_facts = _blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)
    s = _Svc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", lambda *a, **k: _Client())
    asyncio.run(memory_digest.run_turn_digest(user, turn, session_id=SESSION, source="voice_turn_digest"))
    return s.ingested


def _digest_with_claim(monkeypatch, claim, fact, turn):
    """``_digest`` with the extractor's claim row for the one fact (as ``ZOE_STRUCTURAL_CLAIMS=enforce`` reads it)."""
    monkeypatch.setattr(memory_digest, "_read_claims", lambda facts: [claim] * len(facts))
    seen = []

    async def post(svc, user_id, user_message, stored, claims, consumed, result):
        seen.extend(claims)

    monkeypatch.setattr(memory_digest, "_structural_post", post)
    return _digest(monkeypatch, MEMBER, fact, turn), seen


def test_the_digest_stores_the_corrected_value_with_the_correction_as_evidence(monkeypatch):
    asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=FakeSvc([])))
    ingested = _digest(monkeypatch, MEMBER, "User manages a pike shop.", MISHEARD_TURN)
    assert [t for t, _ in ingested] == ["User manages a bike shop."]
    kw = ingested[0][1]
    assert "bike shop" in kw["anchor_text"] and MISHEARD_TURN in kw["anchor_text"]
    assert "corrected" in kw["tags"]


def test_control_without_a_correction_the_digest_stores_what_was_heard(monkeypatch):
    ingested = _digest(monkeypatch, MEMBER, "User manages a pike shop.", MISHEARD_TURN)
    assert [t for t, _ in ingested] == ["User manages a pike shop."]


# ── codex review round 2 (#2058) ─────────────────────────────────────────────

def _claim(obj):
    import structural_claims as sc

    return sc.Claim(subj="user", pred="occupation", obj=obj, pol="affirm", mod="asserted", tense="current",
                    quote="I manage a pike shop")


def test_a_late_digest_rebuilds_the_claim_for_the_corrected_value(monkeypatch):
    asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=FakeSvc([])))
    ingested, seen = _digest_with_claim(monkeypatch, _claim("manages a pike shop"), "User manages a pike shop.", MISHEARD_TURN)
    assert [t for t, _ in ingested] == ["User manages a bike shop."]
    claim = ingested[0][1]["claim"]
    assert claim.obj == "manages a bike shop" and "bike shop" in claim.quote          # never the stale value
    assert seen and seen[0].obj == "manages a bike shop"                                # the post-write claim pass too


def test_a_claim_whose_value_does_not_hold_the_old_value_is_dropped_not_kept_stale(monkeypatch):
    asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=FakeSvc([])))
    ingested, _ = _digest_with_claim(monkeypatch, _claim("runs a shop"), "User manages a pike shop.", MISHEARD_TURN)
    assert [t for t, _ in ingested] == ["User manages a bike shop."] and "claim" not in ingested[0][1]


def test_control_without_a_correction_the_claim_is_the_extractors(monkeypatch):
    ingested, _ = _digest_with_claim(monkeypatch, _claim("manages a pike shop"), "User manages a pike shop.", MISHEARD_TURN)
    assert ingested[0][1]["claim"].obj == "manages a pike shop"


def test_the_correcting_words_survive_the_evidence_cap(monkeypatch):
    from memory_service import scrub_source_excerpt

    long_turn = MISHEARD_TURN + " and " + " ".join(["we had a long chat about the weather and the garden"] * 12)
    assert len(long_turn) > 500

    async def history(user_id, session_id, current):
        return [long_turn]

    monkeypatch.setattr(ca, "_recent_user_messages", history)
    asyncio.run(ca.maybe_fix_recent(CORRECTION, MEMBER, SESSION, svc=FakeSvc([])))
    ingested = _digest(monkeypatch, MEMBER, "User manages a pike shop.", long_turn)
    kw = ingested[0][1]
    stored = scrub_source_excerpt(kw["source_excerpt"])          # what the write boundary keeps
    assert "bike shop" in stored and "bike shop" in kw["anchor_text"][:200]


@pytest.mark.parametrize("text,allowed", [
    ("That's wrong, my sister is Marisol not Marisa.", True),     # a relation pins the row: the store-wide swap may act
    ("it's Thursday not Friday", True),                           # no correction cue: the session path does not read it
    ("actually Perth, not Sydney", False),                        # generic: the session-bounded path only
    ("No, Marisol, not Marisa.", False),
    ("it's actually Perth, not Sydney", False),
])
def test_global_swap_allowed(text, allowed):
    assert ca.global_swap_allowed(text) is allowed


def _tier(monkeypatch, text, fix_result):
    import fast_tiers

    order = []

    async def no_apply(*_a, **_k):
        return None

    async def fix(*_a, **_k):
        order.append("recent")
        return fix_result

    async def swap(*_a, **_k):
        order.append("global")
        return None

    monkeypatch.setattr(ca, "maybe_apply", no_apply)
    monkeypatch.setattr(ca, "maybe_fix_recent", fix)
    monkeypatch.setattr(ca, "maybe_swap", swap)
    asyncio.run(fast_tiers._conversation_quality_tier(text, MEMBER, SESSION, True))
    return order


def test_the_session_bounded_correction_runs_before_the_global_swap(monkeypatch):
    # a generic correction: the narrow path only - the global swap never sees it, whatever the narrow path found
    assert _tier(monkeypatch, "actually Perth, not Sydney", None) == ["recent"]
    # a relation-bearing correction the narrow path did not settle: then the global swap
    assert _tier(monkeypatch, "That's wrong, my sister is Marisol not Marisa.", None) == ["recent", "global"]
    # the narrow path settled it: nothing else runs
    done = ca.CorrectionResult("recent", "Got it.", ["x"])
    assert _tier(monkeypatch, "That's wrong, my sister is Marisol not Marisa.", done) == ["recent"]
