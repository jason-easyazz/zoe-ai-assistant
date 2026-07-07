"""Unit tests for research_evidence.classify_query — personal-scope defer.

A search over the user's OWN notes/journal/memory is note_search / recall, NOT
web research, and must not trip the "Before I start research…" stall (#1099
follow-up). Genuine web research ("find me the cheapest flight") is unaffected.

research_evidence is stdlib-only, so this suite is slim-dep green (ci_safe).
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "services", "zoe-data", "research_evidence.py",
)


def _load_research_evidence():
    spec = importlib.util.spec_from_file_location("research_evidence", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    # Register before exec so @dataclass can resolve cls.__module__ in sys.modules.
    sys.modules["research_evidence"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def re_mod():
    return _load_research_evidence()


# Personal-data search → NOT research (routes to note_search / recall).
PERSONAL_NOT_RESEARCH = [
    "Search my notes for the wifi password",
    "search my notes for the wifi password",
    "find in my notes about the trip",
    "search my journal for the recipe",
    "look through my notes for the door code",
    "search my memory for jason's birthday",
]

# Genuine web research → still "research".
STILL_RESEARCH = [
    "find me the cheapest flight",
    "search for the best noise-cancelling headphones",
    "look up prices for a new tv",
    "recommend me a good italian restaurant",
    "where can i buy a raspberry pi",
]


class TestClassifyQueryPersonalScope:
    def test_personal_note_search_is_not_research(self, re_mod):
        for msg in PERSONAL_NOT_RESEARCH:
            assert re_mod.classify_query(msg) != "research", (
                f"personal note search wrongly classified as research: {msg!r}"
            )

    def test_web_research_stays_research(self, re_mod):
        for msg in STILL_RESEARCH:
            assert re_mod.classify_query(msg) == "research", (
                f"expected research verdict for web search: {msg!r}, "
                f"got {re_mod.classify_query(msg)!r}"
            )
