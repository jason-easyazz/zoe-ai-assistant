"""Tests for the structured JSON logging middleware.

Focused on resilience: the module must import and configure without raising even
when the optional ``python-json-logger`` dependency is absent, and the request
context helpers must behave regardless of formatter availability.
"""

from __future__ import annotations

import pytest
import logging

from starlette.requests import Request

import middleware.logging as mw

pytestmark = pytest.mark.ci_safe


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


def test_log_with_context_unknown_level_falls_back(caplog):
    # A bad level must not silently drop the message.
    with caplog.at_level(logging.INFO):
        mw.log_with_context("warn", "still logged")
    assert any("still logged" in r.message for r in caplog.records)


def test_middleware_mints_unique_request_id_and_echoes_header():
    import asyncio

    from starlette.responses import PlainTextResponse

    seen = {}

    async def _run(headers):
        captured = {}

        async def call_next(request):
            captured["meta"] = mw.get_request_metadata()
            return PlainTextResponse("ok")

        middleware = mw.StructuredLoggingMiddleware(app=lambda *a, **k: None)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
        request = Request(scope)
        response = await middleware.dispatch(request, call_next)
        return response.headers["X-Request-ID"], captured["meta"]

    # No correlation header → a unique id is minted and echoed back.
    rid1, meta1 = asyncio.run(_run({}))
    rid2, _ = asyncio.run(_run({}))
    assert rid1 and rid1 != rid2
    assert meta1["request_id"] == rid1
    assert meta1["authenticated"] is False

    # Inbound header is preserved.
    rid3, meta3 = asyncio.run(_run({"X-Request-ID": "corr-123", "authorization": "Bearer t"}))
    assert rid3 == "corr-123"
    assert meta3["authenticated"] is True
