"""The panel's 2 s ring-screen poll and the omi-sync heartbeat are quiet polls.

Speed audit 2026-11-14 item A2: ``GET /api/alarms/ringing`` (touch-alarms.js,
POLL_MS = 2000) was about 70% of every line in the app log, so the 10 MB x 6
rotation held under two weeks. These tests drive the REAL middleware and pin
that the quiet rule is unchanged for trouble: a quiet path's healthy 200 drops
to DEBUG, while its 500, its slow 200 and every non-quiet path stay at INFO.
"""
from __future__ import annotations

import logging
import os
import sys
import time

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import middleware.logging as mw  # noqa: E402

pytestmark = pytest.mark.ci_safe

_MW_LOGGER = mw.__name__  # the middleware logs through logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ZOE_LOG_QUIET_POLL_PATHS", raising=False)
    monkeypatch.delenv("ZOE_LOG_QUIET_POLL_SLOW_MS", raising=False)


def _client() -> TestClient:
    async def ok(request):
        return JSONResponse({"now": 0, "ringing": []})

    async def boom(request):
        return JSONResponse({"detail": "x"}, status_code=500)

    async def slow(request):
        time.sleep(0.03)
        return JSONResponse({"now": 0, "ringing": []})

    async def heartbeat(request):
        return JSONResponse({"ok": True, "warnings": []})

    app = Starlette(routes=[
        Route("/api/alarms/ringing", ok, methods=["GET"]),
        Route("/api/alarms/ringing-500", boom, methods=["GET"]),
        Route("/api/alarms/ringing-slow", slow, methods=["GET"]),
        Route("/api/alarms", ok, methods=["GET"]),
        Route("/api/omi/device-status", heartbeat, methods=["POST"]),
    ])
    app.add_middleware(mw.StructuredLoggingMiddleware)
    return TestClient(app)


def _completed(caplog, path):
    return [r for r in caplog.records if r.name == _MW_LOGGER and r.getMessage() == "Request completed"
            and getattr(r, "path", None) == path]


def test_ringing_and_device_status_are_on_the_quiet_list():
    assert "/api/alarms/ringing" in mw.DEFAULT_QUIET_POLL_PATHS
    assert "/api/omi/device-status" in mw.DEFAULT_QUIET_POLL_PATHS
    assert "/api/omi/device-status" in mw.HEARTBEAT_POST_PATHS
    assert mw.is_quiet_poll("/api/alarms/ringing", 200, 12, "GET") is True
    assert mw.is_quiet_poll("/api/omi/device-status", 200, 12, "POST") is True


def test_healthy_ring_poll_is_not_logged_at_info(caplog):
    caplog.set_level(logging.DEBUG, logger=_MW_LOGGER)
    assert _client().get("/api/alarms/ringing").status_code == 200
    recs = _completed(caplog, "/api/alarms/ringing")
    assert len(recs) == 1 and recs[0].levelno == logging.DEBUG


def test_healthy_device_status_heartbeat_is_not_logged_at_info(caplog):
    caplog.set_level(logging.DEBUG, logger=_MW_LOGGER)
    assert _client().post("/api/omi/device-status", json={}).status_code == 200
    recs = _completed(caplog, "/api/omi/device-status")
    assert len(recs) == 1 and recs[0].levelno == logging.DEBUG


def test_a_failing_ring_poll_is_still_logged_at_info(caplog, monkeypatch):
    # The quiet list is fnmatch globs: widen it to the error route so the ONLY
    # thing that differs from the healthy case is the 500.
    monkeypatch.setenv("ZOE_LOG_QUIET_POLL_PATHS", "/api/alarms/ringing*")
    caplog.set_level(logging.DEBUG, logger=_MW_LOGGER)
    assert _client().get("/api/alarms/ringing-500").status_code == 500
    recs = _completed(caplog, "/api/alarms/ringing-500")
    assert len(recs) == 1 and recs[0].levelno == logging.INFO and recs[0].status_code == 500


def test_a_slow_ring_poll_is_still_logged_at_info(caplog, monkeypatch):
    monkeypatch.setenv("ZOE_LOG_QUIET_POLL_PATHS", "/api/alarms/ringing*")
    monkeypatch.setenv("ZOE_LOG_QUIET_POLL_SLOW_MS", "10")
    caplog.set_level(logging.DEBUG, logger=_MW_LOGGER)
    assert _client().get("/api/alarms/ringing-slow").status_code == 200
    recs = _completed(caplog, "/api/alarms/ringing-slow")
    assert len(recs) == 1 and recs[0].levelno == logging.INFO and recs[0].duration_ms >= 10


def test_a_non_quiet_path_is_unchanged(caplog):
    caplog.set_level(logging.DEBUG, logger=_MW_LOGGER)
    assert _client().get("/api/alarms").status_code == 200
    recs = _completed(caplog, "/api/alarms")
    assert len(recs) == 1 and recs[0].levelno == logging.INFO


def test_the_uvicorn_access_twin_drops_the_healthy_ring_poll_only():
    flt = mw.QuietPollAccessFilter()

    def rec(verb, path, status):
        return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d',
                                 ("172.18.0.3:1", verb, path, "1.1", status), None)

    assert flt.filter(rec("GET", "/api/alarms/ringing", 200)) is False
    assert flt.filter(rec("GET", "/api/alarms/ringing", 500)) is True
    assert flt.filter(rec("POST", "/api/alarms/ringing", 200)) is True      # a write is never a poll
    assert flt.filter(rec("POST", "/api/omi/device-status", 200)) is False
    assert flt.filter(rec("POST", "/api/omi/device-status", 503)) is True
    assert flt.filter(rec("PUT", "/api/omi/device-status", 200)) is True
