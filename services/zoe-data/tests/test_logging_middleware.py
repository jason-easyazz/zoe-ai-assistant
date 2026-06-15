"""Tests for the structured JSON logging middleware.

Focused on resilience: the module must import and configure without raising even
when the optional ``python-json-logger`` dependency is absent, and the request
context helpers must behave regardless of formatter availability.
"""

from __future__ import annotations

import logging

import middleware.logging as mw


def test_module_imports_without_optional_dependency():
    # Importing the module must never hard-fail; availability is a boolean flag.
    assert isinstance(mw._JSON_LOGGING_AVAILABLE, bool)


def test_setup_json_logging_does_not_raise():
    # Works on both paths: JSON formatter when present, stdlib fallback when not.
    mw.setup_json_logging()
    assert logging.getLogger().level == logging.INFO


def test_setup_json_logging_fallback_keeps_standard_logging(monkeypatch):
    monkeypatch.setattr(mw, "_JSON_LOGGING_AVAILABLE", False)
    mw.setup_json_logging()  # must not raise even with the dependency forced off
    assert logging.getLogger().level == logging.INFO


def test_request_metadata_defaults_to_empty():
    assert mw.get_request_metadata() == {}


def test_log_with_context_merges_fields_without_raising():
    # Should not raise regardless of the active formatter.
    mw.log_with_context("info", "hello", request_id="abc", custom="x")
