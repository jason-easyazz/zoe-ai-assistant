"""Offline tests: tier wrappers, merge, packet, claim shaping, scrapers.

LAB-ONLY. `labs/` is outside every CI lane (`pytest.ini` sets
`testpaths = services/zoe-data/tests`), so these carry NO `ci_safe` marker —
marking them would claim a CI coverage they do not have. Run by hand:

    cd labs/web-search-spike && python3 -m pytest tests -q

No network. The `ddgs` tier is exercised through an injected fake searcher, so
these stay deterministic whether or not engines are reachable. Live probes live
in `probe_engines.py` and `eval/`, deliberately separate.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from websearch import engines, tavily
from websearch.claim import build_check_queries, contradiction_query, is_challenge, neutral_query
from websearch.engines import EnginesBlocked, Result, clean_text, is_blocked
from websearch.extract import _strip_jina_header
from websearch.merge import consensus_merge, dedup_key
from websearch.packet import estimate_tokens, format_packet
from websearch.scrapers import parse_hackernews, parse_wikipedia

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_control_cache():
    """The reachability verdict is memoised; tests must not inherit it."""
    engines.reset_control_cache()
    yield
    engines.reset_control_cache()


# --- ddgs tier: the block-vs-empty distinction -----------------------------
# This is the whole reason a wrapper exists. ddgs raises
# DDGSException("No results found.") for BOTH conditions (ddgs/ddgs.py:454),
# measured live 2026-08-03 against a blocked DuckDuckGo.

ROWS = [{"title": "Canberra", "href": "https://en.wikipedia.org/wiki/Canberra", "body": "capital"}]


def test_search_returns_results():
    got = engines.search("q", searcher=lambda *a: ROWS)
    assert len(got) == 1
    assert got[0].url == "https://en.wikipedia.org/wiki/Canberra"
    assert got[0].engine == "ddgs:auto"


def test_blocked_engines_raise_rather_than_returning_empty():
    """NEGATIVE CONTROL: total refusal must be an ERROR, not zero results.

    If this returned [], Zoe would tell Jason "there's nothing" when the truth
    is that the lookup never happened.
    """

    def refuse(*_args):
        raise RuntimeError("No results found.")

    with pytest.raises(EnginesBlocked):
        engines.search("q", searcher=refuse)


def test_genuinely_empty_query_returns_empty_not_blocked():
    """The other half of the control: reachable engines + no hits => []."""

    def only_control(query, *_args):
        # The control query answers; the caller's query genuinely has no hits.
        return ROWS if query == engines.CONTROL_QUERY else []

    assert engines.search("zzz no such thing", searcher=only_control) == []


def test_empty_result_set_while_unreachable_is_blocked():
    """An empty list is ambiguous too, and gets the same disambiguation.

    Everything returns nothing — including the control — so the only honest
    reading is "we are blocked", not "your query has no answer".
    """
    with pytest.raises(EnginesBlocked):
        engines.search("q", searcher=lambda *a: [])


def test_engines_reachable_is_cached():
    calls = []

    def counting(*_args):
        calls.append(1)
        return ROWS

    engines.engines_reachable(searcher=counting)
    engines.engines_reachable(searcher=counting)
    assert len(calls) == 1, "control probe should be memoised, not re-issued per failure"


def test_search_by_backend_reports_failures_by_name():
    """Provenance: a dead backend must be NAMED, never silently omitted."""

    def selective(_query, backend, *_args):
        if backend == "duckduckgo":
            raise RuntimeError("blocked")
        return ROWS if backend == "brave" else []

    out = engines.search_by_backend("q", backends=("duckduckgo", "brave", "google"), searcher=selective)
    assert out["duckduckgo"].startswith("FAILED")
    assert isinstance(out["brave"], list)
    assert out["google"] == "BLOCKED_OR_EMPTY"


def test_is_blocked_detects_challenges_in_raw_bodies():
    assert is_blocked(fixture("ddg_anomaly.html"))
    assert is_blocked("<html><title>Captcha</title></html>")
    assert not is_blocked("<html><body>Canberra is the capital.</body></html>")


def test_clean_text_strips_tags_and_entities():
    assert clean_text("<b>Canberra</b>  is&nbsp;the &amp; capital") == "Canberra is the & capital"


# --- Tavily free tier ------------------------------------------------------

def test_tavily_parses_real_recorded_response():
    """Parses a REAL recorded response, not a hand-written guess at the shape.

    The previous fixture was synthetic (no key was configured when it was
    written) and it was wrong where it counted: 3 results instead of 6, and no
    `answer`/`raw_content`/`images`/`follow_up_questions` keys at all. Parsing
    a fixture we invented only ever proved the parser matched our own
    assumptions.
    """
    payload = json.loads(fixture("tavily_response.json"))
    assert payload["_fixture_note"].startswith("REAL, RECORDED")

    results = tavily.parse_tavily(payload)
    assert len(results) == 6
    assert results[0].engine == "tavily-free"
    assert results[0].url == "https://en.wikipedia.org/wiki/Canberra"
    assert results[0].extra["score"] == pytest.approx(0.9394, abs=1e-3)
    # Ranks are dense and ordered as returned.
    assert [r.rank for r in results] == [0, 1, 2, 3, 4, 5]
    assert all(r.title and r.url.startswith("https://") for r in results)


def test_tavily_tolerates_the_real_null_fields():
    """`raw_content` is null and `answer` absent on the free/basic tier."""
    payload = json.loads(fixture("tavily_response.json"))
    assert all(row["raw_content"] is None for row in payload["results"])
    results = tavily.parse_tavily(payload)
    assert all(isinstance(r.snippet, str) for r in results)


def test_recorded_fixture_contains_no_api_key():
    """A recorded fixture is response data; it must never carry a credential.

    `_maybe_capture` writes the RESPONSE only — the request body holds
    `api_key` and is never serialised. This pins that, so re-recording can
    never quietly commit a key.
    """
    payload = json.loads(fixture("tavily_response.json"))
    # Scan the DATA, not the `_fixture_note` prose — the note legitimately
    # names the field it is promising never to contain.
    payload.pop("_fixture_note", None)
    raw = json.dumps(payload)
    assert "api_key" not in raw
    # Tavily keys are `tvly-` prefixed; catch any that ever leaks in.
    assert "tvly-" not in raw
    assert "request_id" not in raw, "per-call account correlation id should be stripped"


def test_tavily_unconfigured_raises_rather_than_scoring_zero(monkeypatch):
    """An unconfigured tier and a failing tier are different findings."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert tavily.configured() is False
    with pytest.raises(tavily.TavilyUnconfigured):
        tavily.search("q")


def test_tavily_budget_blocks_when_spent(tmp_path, monkeypatch):
    """The 33/day free ceiling is enforced locally, before the request."""
    monkeypatch.setenv("TAVILY_API_KEY", "fake")
    monkeypatch.setenv("ZOE_TAVILY_DAILY_BUDGET", "2")
    store = tmp_path / "budget.json"
    store.write_text(json.dumps({"day": tavily._today(), "used": 2}))
    assert tavily.budget_state(store).remaining == 0
    with pytest.raises(tavily.TavilyBudgetExhausted):
        tavily.search("q", budget_path=store)


def test_tavily_budget_resets_on_a_new_day(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_TAVILY_DAILY_BUDGET", "33")
    store = tmp_path / "budget.json"
    store.write_text(json.dumps({"day": "1999-01-01", "used": 99}))
    assert tavily.budget_state(store).used == 0


# --- Jina extract ----------------------------------------------------------

def test_jina_header_is_stripped():
    body = "Title: Canberra\n\nURL Source: https://x\n\nMarkdown Content:\nCanberra is the capital."
    assert _strip_jina_header(body) == "Canberra is the capital."


def test_jina_body_without_header_survives():
    assert _strip_jina_header("Plain body text.") == "Plain body text."


# --- merge (cross-TIER) ----------------------------------------------------

@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://www.example.com/x", "https://example.com/x"),
        ("https://example.com/x/", "https://example.com/x"),
        ("https://example.com/x#frag", "https://example.com/x"),
        ("https://EXAMPLE.com/x", "https://example.com/x"),
    ],
)
def test_dedup_key_collapses_url_variants(a, b):
    assert dedup_key(a) == dedup_key(b)


def test_dedup_key_keeps_query_string():
    assert dedup_key("https://example.com/x?a=1") != dedup_key("https://example.com/x?a=2")


def test_consensus_outranks_single_tier_position():
    """A URL two TIERS agree on beats one only a single tier ranked first."""
    solo = [Result(title="Solo", url="https://solo.example/a", engine="ddgs")]
    shared_a = [
        Result(title="Solo", url="https://solo.example/a", engine="ddgs"),
        Result(title="Shared", url="https://shared.example/b", engine="ddgs"),
    ]
    shared_b = [Result(title="Shared", url="https://shared.example/b", engine="tavily-free")]
    merged = consensus_merge([shared_a, shared_b, solo])
    assert merged[0].url == "https://shared.example/b"
    assert merged[0].engines == 2


def test_merge_keeps_the_longest_snippet():
    a = [Result(title="T", url="https://x.example/p", snippet="short", engine="ddgs")]
    b = [Result(title="T", url="https://www.x.example/p/", snippet="a much longer snippet", engine="tavily-free")]
    merged = consensus_merge([a, b])
    assert len(merged) == 1
    assert merged[0].snippet == "a much longer snippet"


# --- scrapers --------------------------------------------------------------

def test_wikipedia_extract_parses():
    extract = parse_wikipedia(json.loads(fixture("wikipedia_extract.json")), "https://en.wikipedia.org/wiki/Canberra")
    assert extract is not None
    assert extract.title == "Canberra"
    assert "capital" in extract.text.lower()


def test_wikipedia_missing_page_returns_none():
    assert parse_wikipedia({"query": {"pages": {"-1": {"missing": ""}}}}, "https://en.wikipedia.org/wiki/Nope") is None


def test_hackernews_search_parses():
    extract = parse_hackernews(json.loads(fixture("hackernews_search.json")), "https://news.ycombinator.com/item?id=1")
    assert extract is not None
    assert extract.source == "hackernews"
    assert extract.title


def test_hackernews_empty_returns_none():
    assert parse_hackernews({"hits": []}, "https://news.ycombinator.com/item?id=1") is None


# --- packet ----------------------------------------------------------------

def _many(n: int) -> list[Result]:
    return [
        Result(title=f"Result number {i} with a fairly long title", url=f"https://site{i}.example/page",
               snippet="A snippet. " * 40, engine="ddgs")
        for i in range(n)
    ]


@pytest.mark.parametrize("budget", [120, 250, 350, 600])
def test_packet_respects_token_budget(budget):
    assert estimate_tokens(format_packet(_many(10), token_budget=budget)) <= budget


def test_packet_is_compact_enough_for_the_brain():
    packet = format_packet(_many(10), token_budget=350)
    assert estimate_tokens(packet) <= 350
    assert packet.count("\n") <= 6


def test_packet_uses_hosts_not_full_urls():
    packet = format_packet(
        [Result(title="Canberra", url="https://en.wikipedia.org/wiki/Canberra", snippet="s", engine="ddgs")]
    )
    assert "en.wikipedia.org" in packet
    assert "https://en.wikipedia.org/wiki/Canberra" not in packet


def test_empty_input_yields_empty_packet():
    assert format_packet([]) == ""


# --- claim backing ---------------------------------------------------------

@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("are you sure?", True), ("Are you sure about that", True), ("really?", True),
        ("prove it", True), ("double check that", True),
        ("what's the weather", False), ("play some music", False), ("add milk to the list", False),
    ],
)
def test_is_challenge(utterance, expected):
    assert is_challenge(utterance) is expected


def test_neutral_query_strips_hedges_including_punctuation():
    assert neutral_query("Are you sure? Canberra is the capital") == "Canberra is the capital"
    assert neutral_query("is it true that Bali is in Thailand") == "Bali is in Thailand"


def test_contradiction_query_negates_the_assertion():
    assert contradiction_query("Bali is in Thailand") == "Bali is not in Thailand"


def test_contradiction_falls_back_when_no_verb_to_negate():
    assert "debunked" in contradiction_query("Canberra capital 1913")


def test_check_queries_are_distinct_and_ordered():
    queries = build_check_queries("Canberra is the capital of Australia")
    assert queries[0] == "Canberra is the capital of Australia"
    assert queries[1] == "Canberra is not the capital of Australia"
    assert len(set(queries)) == len(queries)


# --- end-to-end packet, replayed from fixtures -----------------------------

def test_pipeline_produces_a_budgeted_packet():
    """Tavily + ddgs-shaped results + a Wikipedia extract -> one small packet."""
    tavily_results = tavily.parse_tavily(json.loads(fixture("tavily_response.json")))
    ddgs_like = [
        Result(title="Canberra - Wikipedia", url="https://en.wikipedia.org/wiki/Canberra",
               snippet="Canberra is the capital city of Australia.", engine="ddgs:auto"),
        Result(title="Australia - Wikipedia", url="https://en.wikipedia.org/wiki/Australia",
               snippet="Australia is a country.", engine="ddgs:auto"),
    ]
    merged = consensus_merge([tavily_results, ddgs_like], limit=6)
    assert merged[0].engines == 2, "the URL both tiers returned must lead"
    extract = parse_wikipedia(json.loads(fixture("wikipedia_extract.json")), merged[0].url)
    packet = format_packet(merged, extract=extract, token_budget=350)
    assert "[wikipedia]" in packet
    assert "[web]" in packet
    assert estimate_tokens(packet) <= 350
