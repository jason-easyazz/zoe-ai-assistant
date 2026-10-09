"""A place fact is a FULL CLAUSE, never a trailing fragment (bar S24, 2026-10-09).

The deterministic extractor's "I live in (.{2,80})" took everything after "live in" to the end of the sentence, so the owner's
"what do you know about me?" read back, under Places:

    you live in these days            <- the QUESTION "Which city do I live in these days?"
    you live in Hobart now            <- "Big news - I've moved. I live in Hobart now."
    you live in Dunedin, by the way   <- "I live in Dunedin, by the way."

The guard sits at two levels: the extractor stores the place without its discourse tail and stores nothing for a question or a value
that names no place, and the shared write-quality gate (``is_storable_fact``, which every writer reaches) refuses a place fact whose
value is only such words. Synthetic phrases only; each assertion has a control that turns it red when the guard is removed.
"""
from __future__ import annotations

import pytest

import memory_extractor as mx
import memory_quality as mq

pytestmark = pytest.mark.ci_safe

# the three fragments the acceptance run read back, with the turn that produced each
FRAGMENT_TURNS = [
    ("Which city do I live in these days?", "User lives in these days"),
    ("Big news - I've moved. I live in Hobart now.", "User lives in Hobart now"),
    ("I live in Dunedin, by the way.", "User lives in Dunedin, by the way"),
]


def _texts(turn: str) -> list[str]:
    return [c.text for c in mx._mine_templates(turn, turn, set())]


@pytest.mark.parametrize("turn,fragment", FRAGMENT_TURNS)
def test_the_extractor_never_stores_the_fragment(turn, fragment):
    assert fragment not in _texts(turn)
    assert not [t for t in _texts(turn) if t.lower().startswith("user lives in") and ("now" in t.split()[-1:] or "way" in t or "days" in t)]


def test_the_extractor_stores_the_full_clause_without_the_discourse_tail():
    assert _texts("Big news - I've moved. I live in Hobart now.") == ["User lives in Hobart"]
    assert _texts("I live in Dunedin, by the way.") == ["User lives in Dunedin"]
    assert _texts("I live in Hobart, Tasmania these days.") == ["User lives in Hobart, Tasmania"]
    assert _texts("I live in New York.") == ["User lives in New York"]                       # a plain place is untouched
    assert _texts("I work at the Kestrel clinic nowadays.") == ["User works at/for the Kestrel clinic"]
    assert _texts("I'm from Lisbon, actually.") == ["User is from Lisbon"]


def test_a_question_states_nothing_about_the_speaker():
    for q in ("Which city do I live in these days?", "Where do I work at?", "Do I like pizza?", "What do I enjoy on Sundays?"):
        assert _texts(q) == [], q
    # controls: the same words as a statement are facts, and an aux that is NOT right before the "I" is not a question
    assert _texts("I like pizza.") == ["Preference: user likes pizza"]
    assert _texts("Can you believe I live in Perth") == ["User lives in Perth"]
    assert _texts("Remember that I live in Perth") and any("Perth" in t for t in _texts("Remember that I live in Perth"))


def test_a_place_never_runs_on_into_the_next_sentence():
    assert _texts("I live in Perth. My sister lives in Hobart.") == ["User lives in Perth"]


@pytest.mark.parametrize("fact", ["User lives in these days", "User lives in now", "User lives in the", "User is from here",
                                  "User works at/for these days", "User lives in these days."])
def test_the_write_gate_refuses_a_place_fact_that_names_no_place(fact):
    ok, reason = mq.is_storable_fact(fact)
    assert (ok, reason) == (False, "place_fragment")
    assert mq.place_fragment(fact)


@pytest.mark.parametrize("fact", ["User lives in Hobart", "User lives in Hobart now", "User has moved and now lives in Hobart",
                                  "User lives in Dunedin, by the way", "User works at the library", "User is from Lisbon",
                                  "User's mum lives in Bendigo", "User lives in a house by the sea"])
def test_the_write_gate_keeps_every_real_place_fact(fact):
    assert mq.is_storable_fact(fact)[0] is True and not mq.place_fragment(fact)


def test_clean_place_value():
    for raw, want in [("Hobart now", "Hobart"), ("Dunedin, by the way", "Dunedin"), ("these days", ""), ("now", ""), ("the", ""),
                      ("Hobart", "Hobart"), ("New York", "New York"), ("Perth. My sister", "Perth"), ("", "")]:
        assert mq.clean_place_value(raw) == want, raw


def test_control_without_the_guard_the_three_fragments_come_back(monkeypatch):
    """Break the fix: with the place cleaner and the question guard taken out, the acceptance run's three fragments are stored again."""
    monkeypatch.setattr(mx, "_PLACE_TEMPLATES", frozenset())
    monkeypatch.setattr(mx, "_asked_not_said", lambda *a, **k: False)
    got = [t for turn, _f in FRAGMENT_TURNS for t in _texts(turn)]
    assert "User lives in these days" in got and "User lives in Hobart now" in got
    assert "User lives in Dunedin, by the way" in got
