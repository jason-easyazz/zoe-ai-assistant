"""'I am a X' is not a job: the job/role template needs an occupational frame.

Live bug (2026-10-14): a spoken "I am a <belief/identity phrase>" was filed by the voice_regex
extractor as "User's job/role: ...". A bare "I am a <noun phrase>" is self-description, not
occupation. Synthetic sentences only.
"""

from __future__ import annotations

import re

import pytest

import memory_extractor as me

pytestmark = pytest.mark.ci_safe

# The pre-fix pattern, kept ONLY as a negative control.
_OLD_BROAD = r"i(?:'m|\s+am)\s+a(?:n)?\s+([\w][\w\s]{3,60}?)(?:\s*[.,;!?]|$)"

NOT_JOBS = [
    "I am a man of faith",
    "I'm a night owl",
    "I'm a big fan of jazz",
    "I am a morning person.",
    "I'm a bit tired today",
]
JOBS = [
    ("I work as a nurse", "nurse"),
    ("I'm a carpenter by trade", "carpenter"),
    ("my job is teaching", "teaching"),
    ("I'm employed as a bookkeeper.", "bookkeeper"),
    ("I am a plumber", "plumber"),
    ("I'm a senior software engineer.", "software engineer"),
]


def _job_facts(sentence: str) -> list[str]:
    return [c.text for c in me.extract_candidates(sentence) if c.text.startswith("User's job/role")]


@pytest.mark.parametrize("sentence", NOT_JOBS)
def test_bare_i_am_a_is_not_a_job(sentence):
    assert _job_facts(sentence) == []


@pytest.mark.parametrize("sentence", NOT_JOBS[:3])
def test_negative_control_old_pattern_filed_them(sentence):
    assert re.search(_OLD_BROAD, sentence, re.IGNORECASE)


@pytest.mark.parametrize("sentence,needle", JOBS)
def test_occupational_frames_file_a_job(sentence, needle):
    facts = _job_facts(sentence)
    assert facts and needle in facts[0].lower()


def test_belief_inside_long_utterance_not_a_job():
    assert _job_facts("honestly, I am a man of faith, and I like quiet evenings") == []


@pytest.mark.parametrize("sentence", [
    "Can you guess what my job is based on my hobbies?",
    "Do you know what my occupation is these days?",
    "Guess what my job is based on my hobbies.",
    "Is my job a good fit for night shifts?",
])
def test_possessive_job_questions_file_nothing(sentence):
    assert _job_facts(sentence) == []
