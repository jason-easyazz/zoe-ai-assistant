"""Focused tests for deterministic research evidence helpers."""

import pytest

from research_evidence import (
    classify_query,
    default_source_for_query,
    fetch_web_fallback_results,
    missing_brief_fields,
    package_needs_web_fallback,
)

pytestmark = pytest.mark.ci_safe


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


class _FakeUrlResponse:
    def __init__(
        self,
        body: bytes,
        headers: dict[str, str] | None = None,
        *,
        max_chunk_size: int | None = None,
    ):
        self._body = body
        self.headers = headers or {}
        self._offset = 0
        self._max_chunk_size = max_chunk_size
        self.read_sizes: list[int | None] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int | None = None) -> bytes:
        self.read_sizes.append(size)
        if self._offset >= len(self._body):
            return b""
        limit = len(self._body) if size is None else self._offset + size
        if self._max_chunk_size is not None:
            limit = min(limit, self._offset + self._max_chunk_size)
        chunk = self._body[self._offset : min(limit, len(self._body))]
        self._offset += len(chunk)
        return chunk


def test_fetch_web_fallback_results_parses_normal_bounded_search_html(monkeypatch):
    import research_evidence

    body = b"""
        <a class="result__a" href="https://example.com/deals">Example deal</a>
        <a class="result__snippet">Example deal for $19.99 today</a>
    """
    response = _FakeUrlResponse(body)
    monkeypatch.setattr(research_evidence, "guarded_urlopen", lambda *a, **k: response)
    monkeypatch.setattr(research_evidence, "_fetch_page_price", lambda *a, **k: "")

    rows = fetch_web_fallback_results("example deal", max_results=1)

    assert rows == [
        {
            "title": "Example deal",
            "url": "https://example.com/deals",
            "price": "$19.99",
            "snippet": "Example deal for $19.99 today",
            "verified": "true",
        }
    ]
    assert response.read_sizes == [65536, 65536]


def test_fetch_web_fallback_results_reads_until_eof_after_short_read(monkeypatch):
    import research_evidence

    prefix = b"x" * 4096
    body = prefix + b"""
        <a class="result__a" href="https://example.com/deals">Example deal</a>
        <a class="result__snippet">Example deal for $19.99 today</a>
    """
    response = _FakeUrlResponse(body, max_chunk_size=2048)
    monkeypatch.setattr(research_evidence, "guarded_urlopen", lambda *a, **k: response)
    monkeypatch.setattr(research_evidence, "_fetch_page_price", lambda *a, **k: "")

    rows = fetch_web_fallback_results("example deal", max_results=1)

    assert rows[0]["url"] == "https://example.com/deals"
    assert rows[0]["price"] == "$19.99"
    assert len(response.read_sizes) > 1


def test_fetch_web_fallback_results_rejects_over_cap_search_html(monkeypatch):
    import research_evidence

    cap = 16
    response = _FakeUrlResponse(b"x" * (cap + 1))
    monkeypatch.setattr(research_evidence, "DDG_SEARCH_HTML_MAX_BYTES", cap)
    monkeypatch.setattr(research_evidence, "guarded_urlopen", lambda *a, **k: response)

    assert fetch_web_fallback_results("example deal") == []
    assert response.read_sizes == [cap + 1]
