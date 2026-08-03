"""Offline parser + packet tests. No network, no fixtures regenerated here.

LAB-ONLY. `labs/` is outside every CI lane (`pytest.ini` sets
`testpaths = services/zoe-data/tests`), so these carry NO `ci_safe` marker —
marking them would claim a CI coverage they do not have. Run by hand:

    cd labs/web-search-spike && python3 -m pytest tests -q

Every test here asserts on a RECORDED response. The live-network checks live in
`probe_engines.py`, deliberately separate, so this suite is deterministic.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from websearch.claim import build_check_queries, contradiction_query, is_challenge, neutral_query
from websearch.engines import (
    EngineBlocked,
    Result,
    clean_text,
    is_blocked,
    parse_ddg_html,
    parse_ddg_lite,
    unwrap_ddg_url,
)
from websearch.merge import consensus_merge, dedup_key
from websearch.packet import estimate_tokens, format_packet
from websearch.scrapers import parse_hackernews, parse_wikipedia

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- engines ---------------------------------------------------------------

def test_ddg_html_parses_results():
    results = parse_ddg_html(fixture("ddg_html.html"))
    assert len(results) >= 5
    first = results[0]
    assert first.title
    assert first.url.startswith("https://")
    assert "duckduckgo.com/l/" not in first.url, "redirect wrapper was not unwrapped"
    assert any(r.snippet for r in results), "no snippet survived parsing"


def test_ddg_lite_parses_results_and_drops_ads():
    results = parse_ddg_lite(fixture("ddg_lite.html"))
    assert len(results) >= 5
    assert all(r.url.startswith("https://") for r in results)
    # Sponsored rows route through bing/duckduckgo ad redirectors.
    assert not any("bing.com" in r.url or "/y.js" in r.url for r in results)


def test_both_engines_agree_on_the_top_result():
    """The two endpoints share an index, so consensus should be non-trivial."""
    html = {r.url for r in parse_ddg_html(fixture("ddg_html.html"))}
    lite = {r.url for r in parse_ddg_lite(fixture("ddg_lite.html"))}
    assert html & lite, "no overlap between html/ and lite/ — a parser is likely broken"


def test_bot_challenge_raises_rather_than_returning_empty():
    """NEGATIVE CONTROL: a challenge page must be an ERROR, not zero results.

    This is the whole reason `is_blocked` exists — the block arrives as HTTP
    200/202 with a normal-looking body, so a parser that merely found no
    matches would report 'no results for your query' and the brain would tell
    the user there is nothing, rather than that the lookup failed.
    """
    body = fixture("ddg_anomaly.html")
    assert is_blocked(body)
    with pytest.raises(EngineBlocked):
        parse_ddg_html(body)
    with pytest.raises(EngineBlocked):
        parse_ddg_lite(body)


def test_real_results_are_not_flagged_as_blocked():
    """The other half of the control: no false positives on genuine pages."""
    assert not is_blocked(fixture("ddg_html.html"))
    assert not is_blocked(fixture("ddg_lite.html"))


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FCanberra&rut=x",
         "https://en.wikipedia.org/wiki/Canberra"),
        ("//example.com/page", "https://example.com/page"),
        ("https://example.com/page", "https://example.com/page"),
        ("/relative/only", None),
        ("", None),
    ],
)
def test_unwrap_ddg_url(href, expected):
    assert unwrap_ddg_url(href) == expected


def test_clean_text_strips_tags_and_entities():
    assert clean_text("<b>Canberra</b>  is&nbsp;the &amp; capital") == "Canberra is the & capital"


# --- merge -----------------------------------------------------------------

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


def test_consensus_outranks_single_engine_position():
    """A URL two engines agree on beats a URL only one engine ranked first."""
    solo = [Result(title="Solo", url="https://solo.example/a", engine="e1")]
    shared_a = [
        Result(title="Solo", url="https://solo.example/a", engine="e1"),
        Result(title="Shared", url="https://shared.example/b", engine="e1"),
    ]
    shared_b = [Result(title="Shared", url="https://shared.example/b", engine="e2")]
    merged = consensus_merge([shared_a, shared_b, solo])
    assert merged[0].url == "https://shared.example/b"
    assert merged[0].engines == 2


def test_merge_keeps_the_longest_snippet():
    a = [Result(title="T", url="https://x.example/p", snippet="short", engine="e1")]
    b = [Result(title="T", url="https://www.x.example/p/", snippet="a much longer snippet", engine="e2")]
    merged = consensus_merge([a, b])
    assert len(merged) == 1
    assert merged[0].snippet == "a much longer snippet"


# --- scrapers --------------------------------------------------------------

def test_wikipedia_extract_parses():
    payload = json.loads(fixture("wikipedia_extract.json"))
    extract = parse_wikipedia(payload, "https://en.wikipedia.org/wiki/Canberra")
    assert extract is not None
    assert extract.source == "wikipedia"
    assert extract.title == "Canberra"
    assert "capital" in extract.text.lower()


def test_wikipedia_missing_page_returns_none():
    assert parse_wikipedia({"query": {"pages": {"-1": {"missing": ""}}}}, "https://en.wikipedia.org/wiki/Nope") is None


def test_hackernews_search_parses():
    payload = json.loads(fixture("hackernews_search.json"))
    extract = parse_hackernews(payload, "https://news.ycombinator.com/item?id=1")
    assert extract is not None
    assert extract.source == "hackernews"
    assert extract.title


def test_hackernews_empty_returns_none():
    assert parse_hackernews({"hits": []}, "https://news.ycombinator.com/item?id=1") is None


# --- packet ----------------------------------------------------------------

def _many(n: int) -> list[Result]:
    return [
        Result(title=f"Result number {i} with a fairly long title", url=f"https://site{i}.example/page",
               snippet="A snippet. " * 40, engine="e1")
        for i in range(n)
    ]


@pytest.mark.parametrize("budget", [120, 250, 350, 600])
def test_packet_respects_token_budget(budget):
    packet = format_packet(_many(10), token_budget=budget)
    assert estimate_tokens(packet) <= budget, f"packet overran the {budget}-token budget"


def test_packet_is_compact_enough_for_the_brain():
    """The whole point: 10 raw results must reduce to a few hundred tokens."""
    packet = format_packet(_many(10), token_budget=350)
    assert estimate_tokens(packet) <= 350
    assert packet.count("\n") <= 6


def test_packet_uses_hosts_not_full_urls():
    packet = format_packet(
        [Result(title="Canberra", url="https://en.wikipedia.org/wiki/Canberra", snippet="s", engine="e")]
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
    assert contradiction_query("the panel has 8GB") == "the panel has not 8GB"


def test_contradiction_falls_back_when_no_verb_to_negate():
    assert "debunked" in contradiction_query("Canberra capital 1913")


def test_check_queries_are_distinct_and_ordered():
    queries = build_check_queries("Canberra is the capital of Australia")
    assert queries[0] == "Canberra is the capital of Australia"
    assert queries[1] == "Canberra is not the capital of Australia"
    assert len(set(queries)) == len(queries)


# --- end-to-end pipeline, replayed from fixtures ---------------------------
# The live engines bot-block (README "Findings" #3), so the full
# parse -> merge -> enrich -> packet path is proven here against recorded
# responses rather than depending on an engine being reachable.

def _replayed_packet() -> str:
    batches = [parse_ddg_html(fixture("ddg_html.html")), parse_ddg_lite(fixture("ddg_lite.html"))]
    merged = consensus_merge(batches, limit=6)
    extract = parse_wikipedia(json.loads(fixture("wikipedia_extract.json")), merged[0].url)
    return format_packet(merged, extract=extract, token_budget=350)


def test_pipeline_produces_a_budgeted_packet():
    packet = _replayed_packet()
    assert packet
    assert estimate_tokens(packet) <= 350
    assert "[wikipedia]" in packet
    assert "[web]" in packet


def test_pipeline_ranks_corroborated_results_above_single_engine_ones():
    """The consensus invariant on real recorded pages.

    Deliberately NOT asserting a specific site: which pages land in the trimmed
    window is an artefact of fixture capture, whereas "corroborated outranks
    uncorroborated" is the property the merge exists to provide.
    """
    batches = [parse_ddg_html(fixture("ddg_html.html")), parse_ddg_lite(fixture("ddg_lite.html"))]
    merged = consensus_merge(batches, limit=10)
    counts = [r.engines for r in merged]
    assert counts[0] == 2, "top result should be corroborated by both endpoints"
    assert counts == sorted(counts, reverse=True), "consensus ordering violated"


def test_pipeline_dedups_across_endpoints():
    """The merged list must be shorter than the concatenation — dedup did work."""
    html = parse_ddg_html(fixture("ddg_html.html"))
    lite = parse_ddg_lite(fixture("ddg_lite.html"))
    merged = consensus_merge([html, lite], limit=50)
    assert len(merged) < len(html) + len(lite)
    assert len({dedup_key(r.url) for r in merged}) == len(merged)
