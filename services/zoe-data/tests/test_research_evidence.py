"""Focused tests for deterministic research evidence helpers."""

import pytest

from research_evidence import (
    classify_query,
    default_source_for_query,
    missing_brief_fields,
    package_needs_web_fallback,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("What is photosynthesis", "simple_factual"),
        ("Find me the cheapest flights to Melbourne this weekend", "research"),
        ("Can you help me tidy this note?", "general"),
    ],
)
def test_classify_query_returns_expected_task_class(message, expected):
    assert classify_query(message) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Find sushi", ["location", "budget", "timeframe"]),
        ("Find sushi under $25 this weekend", ["location"]),
        ("Find sushi near Sydney this weekend", ["budget"]),
        ("Find sushi near Sydney under $25", ["timeframe"]),
        ("Find sushi in my area under $25 tonight", []),
    ],
)
def test_missing_brief_fields_reports_location_budget_and_timeframe_gaps(message, expected):
    assert missing_brief_fields(message) == expected


def test_default_source_for_query_builds_duckduckgo_search_url():
    assert (
        default_source_for_query("cheap flights Sydney & Melbourne")
        == "https://duckduckgo.com/?q=cheap+flights+Sydney+%26+Melbourne"
    )


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        ({"sources": [], "results": []}, True),
        (
            {
                "sources": ["https://duckduckgo.com/?q=cheap+flights"],
                "results": [{"value": "$29.00"}],
            },
            True,
        ),
        (
            {
                "sources": ["https://example.com/deals"],
                "results": [],
            },
            True,
        ),
        (
            {
                "sources": ["https://example.com/deals"],
                "results": [{"value": ""}],
            },
            True,
        ),
        (
            {
                "sources": ["https://example.com/deals"],
                "results": [{"value": "$29.00"}],
            },
            False,
        ),
        (
            {
                "sources": ["https://example.com/deals"],
                "results": [{"value": ""}, {"value": ""}],
            },
            False,
        ),
    ],
)
def test_package_needs_web_fallback_depends_on_usable_sources_and_results(package, expected):
    assert package_needs_web_fallback(package) is expected