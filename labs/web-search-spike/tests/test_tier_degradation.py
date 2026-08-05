"""Tavily's three non-answers must stay three DISTINCT findings.

`unconfigured`, `budget-exhausted` and a real API failure are not the same
event, and only one of them — budget exhaustion — is a state the chain is
DESIGNED to survive. Collapsing them into one string (which is what raw
`fan_out` does: `"TavilyBudgetExhausted: ..."`) means the expected degradation
and the two unexpected ones read identically in provenance.

The tests are offline: `TAVILY_API_KEY` and the budget file are both faked, so
no request is ever made.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import websearch  # noqa: E402
from websearch import tavily  # noqa: E402


@pytest.fixture
def budget_file(tmp_path, monkeypatch):
    """Point the budget counter at a temp file. Never touches ~/.zoe."""
    store = tmp_path / "budget.json"
    monkeypatch.setattr(tavily, "BUDGET_FILE", store)
    return store


@pytest.fixture
def no_engines(monkeypatch):
    """Stub the ddgs tier so these tests never reach the network."""
    monkeypatch.setattr(websearch, "ddgs_search", lambda *a, **k: [])
    return None


def _spend(n: int, budget_file):
    for _ in range(n):
        tavily._record_spend(budget_file)


# --- status line ------------------------------------------------------------

def test_status_says_unconfigured_when_there_is_no_key(monkeypatch, budget_file):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert "unconfigured" in websearch.tier_status()["tavily-free"]


def test_status_says_ready_with_budget_left(monkeypatch, budget_file):
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("ZOE_TAVILY_DAILY_BUDGET", "33")
    assert websearch.tier_status()["tavily-free"].startswith("ready (33/33")


def test_status_must_not_say_ready_at_zero_remaining(monkeypatch, budget_file):
    """REGRESSION. Measured 2026-08-03: this reported `ready (0/33 left today)`
    while `_search_tiers` was already refusing to dispatch the tier."""
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("ZOE_TAVILY_DAILY_BUDGET", "3")
    _spend(3, budget_file)

    status = websearch.tier_status()["tavily-free"]
    assert "budget-exhausted" in status
    assert not status.startswith("ready"), "a spent tier must not report itself ready"
    assert "3/3" in status


def test_status_always_names_the_fallback_floor():
    assert "ONLY when a cheaper tier is refused" in websearch.tier_status()["cloakbrowser"]


# --- provenance -------------------------------------------------------------

def test_exhausted_budget_is_named_in_provenance_and_the_tier_is_NOT_dispatched(
    monkeypatch, budget_file, no_engines
):
    """The whole point: the degradation is recorded AND costs nothing.

    Dispatching a tier we know will raise would burn a `fan_out` worker and a
    deadline slot to learn something already on disk.
    """
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("ZOE_TAVILY_DAILY_BUDGET", "2")
    _spend(2, budget_file)

    called = []
    monkeypatch.setattr(tavily, "search", lambda *a, **k: called.append(1) or [])

    _, failures, _ = websearch._search_tiers("emu export block geraldton", 5)

    assert "budget-exhausted" in failures["tavily-free"]
    assert "as designed" in failures["tavily-free"]
    assert called == [], "the exhausted tier was dispatched anyway"


def test_unconfigured_is_a_DIFFERENT_string_from_exhausted(monkeypatch, budget_file, no_engines):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    _, failures, _ = websearch._search_tiers("x", 5)
    assert "unconfigured" in failures["tavily-free"]
    assert "budget" not in failures["tavily-free"]


def test_a_real_tavily_failure_is_neither_of_the_other_two(monkeypatch, budget_file, no_engines):
    """A 500 from Tavily must NOT be reported as a planned degradation."""
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("ZOE_TAVILY_DAILY_BUDGET", "33")

    def _boom(*a, **k):
        raise RuntimeError("HTTP 500 from api.tavily.com")

    monkeypatch.setattr(tavily, "search", _boom)
    _, failures, _ = websearch._search_tiers("x", 5)

    note = failures["tavily-free"]
    assert "HTTP 500" in note
    assert "budget-exhausted" not in note
    assert "unconfigured" not in note


def test_negative_control_a_healthy_tavily_IS_dispatched(monkeypatch, budget_file, no_engines):
    """Without this, the tests above would pass on a chain that never calls
    Tavily at all — 'not dispatched' would be trivially true."""
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("ZOE_TAVILY_DAILY_BUDGET", "33")

    called = []
    monkeypatch.setattr(tavily, "search", lambda *a, **k: called.append(1) or [])

    _, failures, _ = websearch._search_tiers("x", 5)

    assert called == [1], "a healthy Tavily tier was skipped"
    assert "budget-exhausted" not in failures.get("tavily-free", "")
