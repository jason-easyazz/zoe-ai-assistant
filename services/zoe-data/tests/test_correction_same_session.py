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


def row(rid, text, user=MEMBER, session=SESSION, age_s=60):
    return SimpleNamespace(id=rid, text=text, metadata={"user_id": user, "status": "approved", "session_id": session,
                                                         "added_ts": time.time() - age_s})


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
    assert fixed == ("User manages a bike shop.", " ".join(CORRECTION.split()))


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
