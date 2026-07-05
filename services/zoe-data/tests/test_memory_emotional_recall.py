"""Emotional-thread recall wiring (Samantha criterion #2), default OFF behind
ZOE_EMOTIONAL_RECALL_ENABLED.

Two pieces, both no-ops until the flag is on:
  1. `message_needs_emotional_recall` — an emotional-state cue ("how have I been",
     "feeling overwhelmed", "what made me happy") the base recall gate misses.
     Kept SEPARATE from `message_needs_memory` so the default gate is unchanged.
  2. `_build_memory_prompt_packet(boost_emotional=True)` — floats stored
     `emotional_moment` rows (by intensity) ahead of plain facts in the generic
     section, so a heavy user's emotional continuity isn't crowded out of the
     small packet.
"""

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import memory_service
import routers.memories as memories_mod
import zoe_memory_compose as compose_mod
from memory_gate import message_needs_emotional_recall, message_needs_memory
from memory_service import MemoryRef
from routers.memories import (
    _build_memory_prompt_packet,
    _emotional_intensity,
    _emotional_recall_enabled,
    router as memories_router,
)


# ── 1. emotional-cue detection ────────────────────────────────────────────────

# Emotional cues the base gate misses (statements, third-person) MUST be caught
# by the dedicated emotional detector — both valences (the 4B brain under-captures
# joy, so we must not also under-recall it).
@pytest.mark.parametrize("msg", [
    "how have I been feeling",
    "how am I doing lately",
    "I've been so stressed lately",          # statement, not a question
    "feeling really overwhelmed today",
    "what has Jason been worried about",      # third-person
    "I'm still grieving",
    "what made me happy last month",
    "I was so proud of the kids",
])
def test_emotional_cues_fire(msg):
    assert message_needs_emotional_recall(msg) is True, f"{msg!r} should be an emotional cue"


# Non-emotional messages must NOT be treated as emotional cues — including the
# broad-substring false positives that tightening "feeling" / "how am i" fixes.
@pytest.mark.parametrize("msg", [
    "what's the weather",
    "where do I live",
    "add milk to my list",
    "what should I cook for dinner tonight",
    "how do I make pasta",
    "what time is it",
    "I was feeling like pizza tonight",        # bare "feeling" must NOT fire
    "what's the feeling in the market",         # bare "feeling" must NOT fire
    "how am I supposed to configure this",      # procedural "how am i" must NOT fire
    "I'm going through my email",               # bare "going through" must NOT fire
])
def test_non_emotional_messages_do_not_fire(msg):
    assert message_needs_emotional_recall(msg) is False, f"{msg!r} is not an emotional cue"


def test_emotional_detector_is_separate_from_base_gate():
    # An emotional STATEMENT with no possessive/question the base gate misses is
    # exactly what the emotional detector adds — proving the two are independent
    # and the base gate is unchanged by this wiring.
    msg = "I've been so stressed lately"
    assert message_needs_memory(msg) is False
    assert message_needs_emotional_recall(msg) is True


# ── 2. packet float ──────────────────────────────────────────────────────────

def _fact(rid, text):
    return SimpleNamespace(id=rid, text=text, metadata={"status": "approved", "memory_type": "fact"})


def _emo(rid, text, intensity):
    return SimpleNamespace(
        id=rid, text=text,
        metadata={"status": "approved", "memory_type": "emotional_moment",
                  "candidate_intensity": intensity},
    )


def test_intensity_sort_key():
    assert _emotional_intensity(_emo("a", "x", 0.9)) == 0.9
    assert _emotional_intensity(_fact("b", "y")) == -1.0          # plain facts sort last
    # garbled/missing intensity on an emotional row still floats ahead of plain facts
    bad = SimpleNamespace(id="c", text="z", metadata={"memory_type": "emotional_moment"})
    assert _emotional_intensity(bad) == 0.0


def test_boost_off_preserves_order():
    facts = [_fact("f1", "ordinary fact one"), _emo("e1", "was anxious about the move", 0.9)]
    packet = _build_memory_prompt_packet(facts, [], max_facts=12, boost_emotional=False)
    lines = packet["packet"].split("\n")
    # default order: plain fact appears before the emotional row (no reordering)
    assert lines.index(next(l for l in lines if "ordinary fact one" in l)) < \
           lines.index(next(l for l in lines if "anxious" in l))


def test_boost_floats_emotional_ahead_of_facts_by_intensity():
    facts = [
        _fact("f1", "ordinary fact one"),
        _fact("f2", "ordinary fact two"),
        _emo("e_low", "mildly pleased about lunch", 0.3),
        _emo("e_high", "was devastated about the loss", 0.95),
    ]
    packet = _build_memory_prompt_packet(facts, [], max_facts=12, boost_emotional=True)
    lines = [l for l in packet["packet"].split("\n") if l.startswith("- ")]
    # both emotional rows lead, highest intensity first, plain facts after
    assert "devastated" in lines[0]
    assert "mildly pleased" in lines[1]
    assert "ordinary fact" in lines[2] and "ordinary fact" in lines[3]


def test_boost_does_not_drop_or_duplicate():
    facts = [_fact("f1", "fact a"), _emo("e1", "felt joy at the news", 0.7)]
    off = _build_memory_prompt_packet(facts, [], max_facts=12, boost_emotional=False)
    on = _build_memory_prompt_packet(facts, [], max_facts=12, boost_emotional=True)
    assert off["count"] == on["count"] == 2       # same rows, only order differs


# ── 3. flag reader ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("off", False), ("", False), ("nope", False),
])
def test_flag_reader(monkeypatch, val, expected):
    monkeypatch.setenv("ZOE_EMOTIONAL_RECALL_ENABLED", val)
    assert _emotional_recall_enabled() is expected


def test_flag_reader_unset(monkeypatch):
    monkeypatch.delenv("ZOE_EMOTIONAL_RECALL_ENABLED", raising=False)
    assert _emotional_recall_enabled() is False


# ── 4. endpoint wiring (flag gates BOTH the search fire and the packet float) ──

def _emo_ref(rid, text, intensity):
    return MemoryRef(
        id=rid, text=text, score=0.9,
        metadata={"status": "approved", "memory_type": "emotional_moment",
                  "candidate_intensity": intensity},
    )


def _plain_ref(rid, text):
    return MemoryRef(id=rid, text=text, score=0.0,
                     metadata={"status": "approved", "memory_type": "fact"})


# The crowded-out emotional row. It is the WORST case the live test exposed: last
# in the fact window (so truncated from the generic top-12) AND not returned by
# semantic search — so ONLY the explicit pin can surface it.
_EMO = _emo_ref("emo00001", "Jason has been anxious about the settlement", 0.9)
_HEAVY_FACTS = [_plain_ref(f"f{i}", f"ordinary fact {i}") for i in range(12)] + [_EMO]


class _RecordingSvc:
    """Fake MemoryService. `search` returns only what it's told (default: nothing
    relevant), so a passing 'emotional row surfaced' assertion can only be the pin
    — not search luck. `load_for_prompt` returns the full window (emo row last),
    which the packet's max_facts truncates but `_fetch_emotional_moments` scans."""

    def __init__(self, facts, search_hits=None):
        self._facts = facts
        self._search_hits = search_hits or []
        self.searched = False

    async def load_for_prompt(self, user_id, *, limit=20):
        return self._facts[:limit]

    async def search(self, q, *, user_id, limit=10):
        self.searched = True
        return list(self._search_hits)


@pytest.fixture
def _wire(monkeypatch):
    """Internal token + non-guest + a heavy fake svc (emo row crowded out and NOT
    returned by search) + stubbed compose so the endpoint needs no DB."""
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda uid: uid == "guest")
    svc = _RecordingSvc(facts=_HEAVY_FACTS)          # search returns nothing relevant
    monkeypatch.setattr(memories_mod, "_svc", lambda: svc)

    async def _no_compose(user_id, message):
        return {}
    monkeypatch.setattr(compose_mod, "compose_packet", _no_compose)
    return svc


def _app_with_router():
    app = FastAPI()
    app.include_router(memories_router)
    return app


def _get(message):
    return TestClient(_app_with_router()).get(
        "/api/memories/for-prompt",
        params={"user_id": "jason", "message": message},
        headers={"X-Internal-Token": "tok"},
    )


# ── _pick_emotional_moments unit ──────────────────────────────────────────────

def test_pick_emotional_moments_filters_and_orders():
    rows = [
        _plain_ref("p", "ordinary fact"),
        _emo_ref("e_low", "mildly annoyed", 0.2),
        _emo_ref("e_high", "devastated", 0.95),
    ]
    got = memories_mod._pick_emotional_moments(rows)
    assert [r.id for r in got] == ["e_high", "e_low"]     # type-filtered, intensity desc


def test_pick_emotional_moments_caps_at_max():
    rows = [_emo_ref(f"e{i}", f"moment {i}", 0.9 - i * 0.1) for i in range(6)]
    got = memories_mod._pick_emotional_moments(rows)
    assert len(got) == memories_mod._EMO_PIN_MAX
    assert [r.id for r in got] == ["e0", "e1", "e2"]      # highest-intensity first


# ── endpoint wiring (pin is the load-bearing mechanism) ───────────────────────

def test_flag_off_emotional_statement_is_noop(monkeypatch, _wire):
    # "I've been so stressed" is missed by the base gate; flag OFF ⇒ no emotional
    # detector, no search, no pin ⇒ the crowded-out row stays lost.
    monkeypatch.delenv("ZOE_EMOTIONAL_RECALL_ENABLED", raising=False)
    resp = _get("I've been so stressed lately")
    assert resp.status_code == 200
    assert _wire.searched is False
    assert "anxious about the settlement" not in resp.json()["packet"]


def test_flag_on_pins_crowded_out_row(monkeypatch, _wire):
    # Flag ON: search returns NOTHING relevant, yet the pinned emotional moment
    # surfaces AND leads — proving the pin (not search) carries continuity.
    monkeypatch.setenv("ZOE_EMOTIONAL_RECALL_ENABLED", "1")
    resp = _get("I've been so stressed lately")
    assert resp.status_code == 200
    packet = resp.json()["packet"]
    assert "anxious about the settlement" in packet
    first_line = next(l for l in packet.split("\n") if l.startswith("- "))
    assert "anxious about the settlement" in first_line


def test_flag_on_pin_dedups_with_search(monkeypatch):
    # If search ALSO returns the emotional row, the packet must not double-count it.
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda uid: uid == "guest")
    svc = _RecordingSvc(facts=_HEAVY_FACTS, search_hits=[_EMO])   # search returns the emo row too
    monkeypatch.setattr(memories_mod, "_svc", lambda: svc)

    async def _no_compose(user_id, message):
        return {}
    monkeypatch.setattr(compose_mod, "compose_packet", _no_compose)
    monkeypatch.setenv("ZOE_EMOTIONAL_RECALL_ENABLED", "1")
    packet = _get("I've been so stressed lately").json()["packet"]
    assert packet.count("anxious about the settlement") == 1


def test_flag_on_neutral_turn_does_not_fire(monkeypatch, _wire):
    # Flag ON but a non-emotional turn that trips NEITHER the base gate nor the
    # emotional cue: no search, no pin — a true no-op on ordinary traffic.
    monkeypatch.setenv("ZOE_EMOTIONAL_RECALL_ENABLED", "1")
    resp = _get("what's the weather tomorrow")
    assert resp.status_code == 200
    assert _wire.searched is False
    assert "anxious about the settlement" not in resp.json()["packet"]
