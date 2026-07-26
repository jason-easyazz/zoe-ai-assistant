"""Packet-level near-dedup for /for-prompt — collapses the near-duplicate rows
the write-time gate didn't merge ("My mum likes ncis." / "your mum likes NCIS.")
so the small packet's slots aren't wasted, WITHOUT dropping distinct, richer, or
contradictory facts.
"""
from types import SimpleNamespace

import pytest

from routers.memories import (
    _build_memory_prompt_packet,
    _dedup_tokens,
    _is_near_duplicate,
)

pytestmark = pytest.mark.ci_safe


def _ref(rid, text, status="approved"):
    return SimpleNamespace(id=rid, text=text, metadata={"status": status, "memory_type": "fact"})


# --- helper: collapse repeats, keep distinct / richer / contradictory ---
@pytest.mark.parametrize("a,b,near", [
    ("My mum likes ncis.", "your mum likes NCIS.", True),        # same fact, different phrasing
    ("My mum's name is Janice", "My mum's birthday is 17 Nov 1947", False),  # distinct
    ("User lives in Geraldton", "User lives in Perth", False),   # contradictory — keep both
    ("My dad's name is Neil",
     "My dad's name is Neil. My mum likes ncis. I have two sisters.", False),  # richer superset
    # Greptile P1: a slightly-richer candidate that ADDS a token must survive.
    ("My dad's name is Neil", "My dad Neil lives in Perth", False),
])
def test_near_duplicate_judgement(a, b, near):
    assert _is_near_duplicate(_dedup_tokens(b), [_dedup_tokens(a)]) is near


def test_packet_collapses_near_dupes_and_keeps_distinct():
    refs = [
        _ref("a", "My mum likes ncis."),
        _ref("b", "your mum likes NCIS."),        # near-dup of a → dropped
        _ref("c", "My mum's name is Janice."),    # distinct → kept
        _ref("d", "User lives in Geraldton"),     # distinct → kept
        _ref("e", "User lives in Perth"),          # contradictory, NOT a dup → kept
    ]
    packet = _build_memory_prompt_packet([], refs, max_facts=12)
    body = packet["packet"].lower()
    assert body.count("likes ncis") == 1              # the repeat collapsed
    assert "janice" in body and "geraldton" in body and "perth" in body  # distinct + contradictory kept
    assert packet["count"] == 4                        # 5 in, 1 dropped


def test_tiny_facts_not_collapsed():
    # A single content token ("i am"/"i'm" are stopwords) → the <2 guard must
    # keep BOTH even though they'd otherwise be 100%-covered near-dupes.
    a, b = _dedup_tokens("I am Teddy"), _dedup_tokens("I'm Teddy")
    assert len(a) == 1 and len(b) == 1            # both reduce to {"teddy"}
    assert _is_near_duplicate(b, [a]) is False    # <2 guard fires
    refs = [_ref("a", "I am Teddy"), _ref("b", "I'm Teddy")]
    assert _build_memory_prompt_packet([], refs, max_facts=12)["count"] == 2


def test_search_hits_keep_their_slot_over_facts():
    # A near-dup present in BOTH hits and facts: the hits phrasing (processed
    # first) must be the one kept, not the facts phrasing. Guards the iteration
    # order the packet relies on.
    hits = [_ref("h", "your mum likes NCIS")]
    facts = [_ref("f", "My mum likes ncis.")]
    packet = _build_memory_prompt_packet(facts, hits, max_facts=12)
    assert packet["count"] == 1
    assert "your mum likes ncis" in packet["packet"].lower()   # hits phrasing won
    assert packet["refs"][0]["id"] == "h"
