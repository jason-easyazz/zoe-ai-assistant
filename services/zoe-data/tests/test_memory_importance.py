"""Importance scoring (Samantha 3b) — content scorer + write-path wiring.

`memory_importance.score_importance` flags high-stakes facts so the 2a hybrid
importance arm can rank them up; `MemoryService._build_metadata` writes it onto a
row's metadata only when > 0 (ordinary facts stay boost-free).
"""

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import pytest

from memory_importance import score_importance
from memory_service import MemoryService


# ── scorer tiers ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Jason is allergic to penicillin", 0.9),
    ("Jason carries an EpiPen for his nut allergy", 0.9),
    ("Jason takes warfarin, a blood thinner", 0.9),
    ("Jason is diabetic and uses insulin", 0.9),
    ("Jason is coeliac and eats gluten-free", 0.7),
    ("Jason is vegetarian", 0.7),
    ("Jason's blood type is O negative", 0.6),
    ("Jason's next of kin is his sister Sara", 0.6),
])
def test_high_stakes_facts_score(text, expected):
    assert score_importance(text) == expected


@pytest.mark.parametrize("text", [
    "Jason likes pizza",
    "Jason loves his dog Pixel",
    "Jason went to the shops today",
    "Jason's favourite colour is blue",
    "Jason studied medicine at university",       # bare "medicine" must NOT fire
    "Jason is interested in alternative medicine",  # (Greptile #1017)
    "",
])
def test_ordinary_facts_score_zero(text):
    # A false-high would wrongly outrank real hits; general sentiment must stay 0.
    assert score_importance(text) == 0.0


def test_safety_outranks_dietary_when_both_present():
    # "allergic" (safety) + "gluten-free" (dietary) → the safety tier wins.
    assert score_importance("Jason is allergic to shellfish and eats gluten-free") == 0.9


# ── write-path wiring (_build_metadata is a staticmethod) ─────────────────────

def _meta(text, memory_type="fact"):
    return MemoryService._build_metadata(
        user_id="jason", source="test", session_id=None, user_turn_id=None,
        memory_type=memory_type, confidence=0.8, status="approved", tags=[],
        entity_type=None, entity_id=None, expires_at=None, text=text,
    )


def test_importance_written_for_high_stakes_fact():
    md = _meta("Jason is allergic to penicillin")
    assert md.get("importance") == 0.9


def test_no_importance_key_for_ordinary_fact():
    md = _meta("Jason likes pizza")
    assert "importance" not in md          # ordinary facts stay boost-free (arm no-op)
