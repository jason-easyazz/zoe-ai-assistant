"""Offline negative control for the eval harness's own block/empty verdict.

LAB-ONLY, like `test_parse.py`: `labs/` is outside every CI lane, so these carry
NO `ci_safe` marker. Run by hand:

    cd labs/web-search-spike && python3 -m pytest tests -q

No network: the engine tier is injected, and Tavily is forced unconfigured via
the environment, which is exactly the box state that produced the bug.

THE RULE UNDER TEST (`run_eval.py` module docstring): *a blocked tier must
report BLOCKED — and only a block may.* The harness used to decide status from
`notes`, and `notes` always contains "tavily unconfigured" when `TAVILY_API_KEY`
is unset, so an empty-but-unblocked engine result was reported as `blocked`
(measured: query `local-05` of the 20260803T112640Z run — no `EnginesBlocked`
was raised). Status now derives from `blocked_tiers`, populated only by an
actual `EnginesBlocked` raise.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

SPIKE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPIKE))

from websearch.engines import EnginesBlocked, Result  # noqa: E402


def _load_run_eval():
    """Import `eval/run_eval.py` by path — `eval/` is not an importable package."""
    spec = importlib.util.spec_from_file_location("_run_eval", SPIKE / "eval" / "run_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_eval = _load_run_eval()


@pytest.fixture(autouse=True)
def _tavily_unconfigured(monkeypatch):
    """Reproduce the box state that caused the mislabel: no Tavily key."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert run_eval.tavily.configured() is False


def test_empty_but_unblocked_is_empty_not_blocked(monkeypatch):
    """NEGATIVE CONTROL: no hits + no block => `empty`, however loud the notes.

    If this said `blocked`, the run sheet would claim the engines refused us on a
    query they actually answered with nothing — the exact inversion of the rule.
    """
    monkeypatch.setattr(run_eval, "ddgs_search", lambda *a, **k: [])

    row = run_eval.combo_all_free("zzz no such thing")

    assert row["status"] == "empty"
    assert row["blocked_tiers"] == []
    # The note is still RECORDED — it is informational, it is just not a verdict.
    assert "tavily unconfigured" in row["notes"]


def test_a_real_block_still_reports_blocked(monkeypatch):
    """The other half: an actual `EnginesBlocked` raise must survive as BLOCKED."""

    def refuse(*_args, **_kwargs):
        raise EnginesBlocked("every engine refused")

    monkeypatch.setattr(run_eval, "ddgs_search", refuse)

    row = run_eval.combo_all_free("q")

    assert row["status"] == "blocked"
    assert row["blocked_tiers"] == ["ddgs"]
    assert any("ddgs blocked" in n for n in row["notes"])


def test_results_do_not_get_a_block_verdict(monkeypatch):
    """A tier that answered is `ok`, and carries an empty `blocked_tiers`."""
    monkeypatch.setattr(
        run_eval,
        "ddgs_search",
        lambda *a, **k: [Result(title="Canberra", url="https://en.wikipedia.org/wiki/Canberra",
                                snippet="capital", engine="ddgs:auto")],
    )
    # No network: the structured-enrichment step is stubbed out.
    monkeypatch.setattr(run_eval, "scrape", lambda *_a, **_k: None)

    row = run_eval.combo_all_free("capital of australia")

    assert row["status"] == "ok"
    assert row["blocked_tiers"] == []
