"""Tests for ui_orchestrator allowed action types."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui_orchestrator import ALLOWED_ACTION_TYPES


def test_panel_navigate_allowed():
    assert "panel_navigate" in ALLOWED_ACTION_TYPES


def test_panel_clear_allowed():
    assert "panel_clear" in ALLOWED_ACTION_TYPES


def test_panel_show_fullscreen_allowed():
    assert "panel_show_fullscreen" in ALLOWED_ACTION_TYPES


def test_panel_announce_allowed():
    assert "panel_announce" in ALLOWED_ACTION_TYPES


def test_panel_request_auth_allowed():
    assert "panel_request_auth" in ALLOWED_ACTION_TYPES


def test_panel_set_mode_allowed():
    assert "panel_set_mode" in ALLOWED_ACTION_TYPES


def test_existing_types_not_broken():
    for t in ("navigate", "open_panel", "focus", "fill", "notify", "refresh", "click"):
        assert t in ALLOWED_ACTION_TYPES, f"Broken: {t} missing from ALLOWED_ACTION_TYPES"
