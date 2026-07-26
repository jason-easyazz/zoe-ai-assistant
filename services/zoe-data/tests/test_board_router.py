"""Tests for the board review endpoint (shaping + safety, no DB)."""
import json
import pytest
from routers import board

pytestmark = pytest.mark.ci_safe


def test_pr_from_details_returns_only_https_github_urls():
    assert board._pr_from_details({"pr_url": "https://github.com/o/r/pull/1"}) == "https://github.com/o/r/pull/1"
    assert board._pr_from_details(json.dumps({"pr_url": "https://github.com/o/r/pull/2"})) == "https://github.com/o/r/pull/2"
    # non-https / hostile schemes are dropped (defence-in-depth vs XSS)
    assert board._pr_from_details({"pr_url": "javascript:alert(1)"}) is None
    assert board._pr_from_details({"pr_url": "u"}) is None
    assert board._pr_from_details({}) is None
    assert board._pr_from_details(None) is None
    assert board._pr_from_details("not json") is None


def test_entry_shapes_number_title_pr_and_reason():
    row = {"number": 5, "title": "t", "details": json.dumps({"pr_url": "https://github.com/o/r/pull/5", "reason": "why"})}
    e = board._entry(row, with_reason=True)
    assert e == {"number": 5, "title": "t", "pr_url": "https://github.com/o/r/pull/5", "reason": "why"}


def test_entry_surfaces_plain_english_summary_for_done():
    row = {"number": 9, "title": "t", "details": json.dumps({
        "pr_url": "https://github.com/o/r/pull/9",
        "summary": "fix(x): guard null", "summary_detail": "Guards the null case."})}
    e = board._entry(row, with_summary=True)
    assert e["summary"] == "fix(x): guard null" and e["summary_detail"] == "Guards the null case."
    # without the flag (in-flight/blocked lists), no summary keys are added
    assert "summary" not in board._entry(row)


def test_str_from_details_rejects_nonstring_and_blank():
    assert board._str_from_details({"summary": 123}, "summary") is None
    assert board._str_from_details({"summary": "   "}, "summary") is None
    assert board._str_from_details({}, "summary") is None
    assert board._str_from_details("not json", "summary") is None
    assert board._str_from_details({"summary": "real"}, "summary") == "real"


def test_summary_route_requires_auth():
    # every route on this router carries the get_current_user dependency
    route = next(r for r in board.router.routes if r.path == "/api/board/summary")
    dep_calls = [d.call for d in route.dependant.dependencies]
    from auth import get_current_user
    assert get_current_user in dep_calls
