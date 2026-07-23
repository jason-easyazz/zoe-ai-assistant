"""Tests for the board review endpoint (shaping logic + route registration)."""
import json
import pytest
from routers import board

pytestmark = pytest.mark.ci_safe


def test_pr_from_details_handles_dict_json_and_none():
    assert board._pr_from_details({"pr_url": "u"}) == "u"
    assert board._pr_from_details(json.dumps({"pr_url": "u2"})) == "u2"
    assert board._pr_from_details({}) is None
    assert board._pr_from_details(None) is None
    assert board._pr_from_details("not json") is None


def test_router_exposes_summary_route():
    paths = {r.path for r in board.router.routes}
    assert "/api/board/summary" in paths
