"""Regression tests for the 2026-10-04 log-review fixes.

See ``docs/knowledge/log-review-2026-10-04.md``. Each test pins one noise/leak
CLASS and was written to go red on the pre-fix code:

* healthy kiosk polls flooded the request log (stderr JSON + uvicorn access);
* httpx / apscheduler executor per-call INFO chatter;
* expected upstream outages logged as ~5 KB tracebacks on every poll;
* repeated identical warnings (reconnect-looping clients) not throttled;
* a routine success trace at WARNING that also printed reply text;
* a 20-char session-id prefix written to the log;
* the app log created world-readable.

``configure_logging`` and the logger levels it sets are process-global, so every
test snapshots/restores them (identity restore — docs/knowledge/test-isolation-playbook.md).
"""
from __future__ import annotations

import asyncio
import logging
import os
import stat
import sys
import types

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import log_throttle  # noqa: E402
import logging_setup  # noqa: E402
import middleware.logging as mw  # noqa: E402

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _isolate_logging(monkeypatch):
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    names = list(logging_setup.CHATTY_LOGGERS) + ["uvicorn.access"]
    saved_levels = {n: logging.getLogger(n).level for n in names}
    saved_filters = {n: list(logging.getLogger(n).filters) for n in names}
    for var in (
        "ZOE_LOG_QUIET_POLL_PATHS",
        "ZOE_LOG_QUIET_POLL_SLOW_MS",
        "ZOE_LOG_CHATTY_LIBS_LEVEL",
        "ZOE_LOG_REPEAT_WINDOW_S",
        "ZOE_LOG_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    log_throttle._state.clear()
    yield
    for h in list(root.handlers):
        if h not in saved_handlers:
            root.removeHandler(h)
            h.close()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for n in names:
        lg = logging.getLogger(n)
        lg.setLevel(saved_levels[n])
        lg.filters[:] = saved_filters[n]
    log_throttle._state.clear()


# ── healthy polls are quiet; trouble never is ────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/ui/actions/pending",
        "/api/ui/state/sync",
        "/api/voice/announcements",
        "/api/system/display/preferences",
        "/api/skybridge/timers",
        "/api/ha/entities",
        "/api/music/now-playing",
        "/api/panels/zoe-touch-pi/config",
        "/health",
    ],
)
def test_healthy_poll_is_quiet(path):
    assert mw.is_quiet_poll(path, 200, 12) is True


def test_trouble_on_a_poll_path_is_never_quiet():
    # an outage is exactly what a poll log exists to show
    assert mw.is_quiet_poll("/api/ha/entities", 503, 5) is False
    assert mw.is_quiet_poll("/api/ui/actions/pending", 401, 1) is False
    # slow is trouble too
    assert mw.is_quiet_poll("/api/ui/actions/pending", 200, 2500) is False


def test_non_poll_paths_are_never_quiet():
    # NEGATIVE CONTROL: the matcher must not be "everything".
    assert mw.is_quiet_poll("/api/voice/transcribe", 200, 5) is False
    assert mw.is_quiet_poll("/api/chat/", 200, 5) is False
    assert mw.is_quiet_poll("/api/panels/x/config/extra", 200, 5) is False


def test_env_overrides_the_poll_list(monkeypatch):
    monkeypatch.setenv("ZOE_LOG_QUIET_POLL_PATHS", "off")
    assert mw.is_quiet_poll("/api/ui/actions/pending", 200, 1) is False
    monkeypatch.setenv("ZOE_LOG_QUIET_POLL_PATHS", "/api/custom/*, /health")
    assert mw.is_quiet_poll("/api/custom/a", 200, 1) is True
    assert mw.is_quiet_poll("/api/ui/actions/pending", 200, 1) is False
    monkeypatch.setenv("ZOE_LOG_QUIET_POLL_SLOW_MS", "garbage")
    assert mw.is_quiet_poll("/health", 200, 999) is True  # falls back to 1000 ms


def _dispatch(path: str, status: int):
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse

    async def call_next(_request):
        return PlainTextResponse("x", status_code=status)

    middleware = mw.StructuredLoggingMiddleware(app=lambda *a, **k: None)
    request = Request({"type": "http", "method": "GET", "path": path, "headers": []})
    return asyncio.run(middleware.dispatch(request, call_next))


def test_middleware_logs_healthy_poll_at_debug_and_trouble_at_info(caplog):
    with caplog.at_level(logging.DEBUG, logger="middleware.logging"):
        _dispatch("/api/ui/actions/pending", 200)
        _dispatch("/api/ui/actions/pending", 503)
        _dispatch("/api/chat/", 200)
    levels = [(r.path if hasattr(r, "path") else "?", r.levelno) for r in caplog.records
              if r.getMessage() == "Request completed"]
    assert levels == [
        ("/api/ui/actions/pending", logging.DEBUG),
        ("/api/ui/actions/pending", logging.INFO),
        ("/api/chat/", logging.INFO),
    ]


def _access_record(path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d',
        ("172.18.0.3:55596", "GET", path, "1.1", status), None,
    )


def test_access_filter_drops_healthy_polls_only():
    flt = mw.QuietPollAccessFilter()
    assert flt.filter(_access_record("/api/ui/actions/pending?panel_id=x&limit=5", 200)) is False
    assert flt.filter(_access_record("/api/ui/actions/pending?panel_id=x", 401)) is True
    assert flt.filter(_access_record("/api/voice/transcribe", 200)) is True
    # fail-open on a record shape we do not recognise
    odd = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, "plain", (), None)
    assert flt.filter(odd) is True


# ── library chatter ──────────────────────────────────────────────────────────


def test_configure_logging_quiets_chatty_libraries_and_is_idempotent(tmp_path):
    logging.getLogger().setLevel(logging.INFO)  # production root level
    for name in logging_setup.CHATTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)
    # control: before configuration an httpx INFO line is emitted at INFO
    assert logging.getLogger("httpx").isEnabledFor(logging.INFO)

    logging_setup.configure_logging(log_dir=tmp_path)
    logging_setup.configure_logging(log_dir=tmp_path)

    for name in logging_setup.CHATTY_LOGGERS:
        lg = logging.getLogger(name)
        assert not lg.isEnabledFor(logging.INFO), name
        assert lg.isEnabledFor(logging.WARNING), name
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, mw.QuietPollAccessFilter) for f in access.filters) == 1


def test_chatty_level_env_restores_the_lines(tmp_path, monkeypatch):
    logging.getLogger().setLevel(logging.INFO)
    monkeypatch.setenv("ZOE_LOG_CHATTY_LIBS_LEVEL", "INFO")
    logging_setup.configure_logging(log_dir=tmp_path)
    assert logging.getLogger("httpx").isEnabledFor(logging.INFO)


def test_app_log_is_not_world_readable(tmp_path):
    handler = logging_setup.configure_logging(log_dir=tmp_path)
    assert handler is not None
    mode = stat.S_IMODE(os.stat(tmp_path / "zoe-data.app.log").st_mode)
    assert mode == 0o640, oct(mode)
    # and again after a rollover (the new segment is a fresh file)
    handler.doRollover()
    mode = stat.S_IMODE(os.stat(tmp_path / "zoe-data.app.log").st_mode)
    assert mode == 0o640, oct(mode)


# ── throttle + upstream-failure helper ───────────────────────────────────────


def test_log_throttled_first_emits_repeats_fold_into_a_count(caplog):
    lg = logging.getLogger("t.throttle")
    with caplog.at_level(logging.WARNING, logger="t.throttle"):
        assert log_throttle.log_throttled(lg, logging.WARNING, "k", "boom %s", "a", window_s=3600) is True
        for _ in range(4):
            assert log_throttle.log_throttled(lg, logging.WARNING, "k", "boom %s", "a", window_s=3600) is False
        # a different key is independent
        assert log_throttle.log_throttled(lg, logging.WARNING, "other", "boom", window_s=3600) is True
        # window elapsed (0 disables throttling) -> emits, carrying the suppressed count
        log_throttle._state["k"][0] -= 7200
        assert log_throttle.log_throttled(lg, logging.WARNING, "k", "boom %s", "a", window_s=3600) is True
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs == ["boom a", "boom", "boom a (+4 similar suppressed)"]


def test_log_throttled_window_zero_disables(caplog):
    lg = logging.getLogger("t.throttle0")
    with caplog.at_level(logging.WARNING, logger="t.throttle0"):
        for _ in range(3):
            assert log_throttle.log_throttled(lg, logging.WARNING, "z", "x", window_s=0) is True
    assert len(caplog.records) == 3


def test_log_throttled_key_space_is_bounded():
    lg = logging.getLogger("t.bound")
    for i in range(log_throttle._MAX_KEYS + 50):
        log_throttle.log_throttled(lg, logging.DEBUG, f"k{i}", "x", window_s=3600)
    assert len(log_throttle._state) <= log_throttle._MAX_KEYS


def test_upstream_timeout_logs_one_line_without_traceback(caplog):
    lg = logging.getLogger("t.upstream")
    with caplog.at_level(logging.WARNING, logger="t.upstream"):
        for _ in range(5):
            log_throttle.log_upstream_failure(lg, "bridge down", httpx.ReadTimeout(""))
    assert len(caplog.records) == 1  # five polls, one line
    rec = caplog.records[0]
    assert rec.levelno == logging.WARNING
    assert rec.exc_info is None
    assert "ReadTimeout" in rec.getMessage()


def test_genuine_bug_keeps_its_traceback_and_is_never_throttled(caplog):
    lg = logging.getLogger("t.upstream2")
    with caplog.at_level(logging.WARNING, logger="t.upstream2"):
        for _ in range(3):
            try:
                {}["missing"]
            except KeyError as exc:
                log_throttle.log_upstream_failure(lg, "bridge parse", exc)
    assert len(caplog.records) == 3
    assert all(r.levelno == logging.ERROR and r.exc_info for r in caplog.records)


def test_ha_entities_timeout_is_502_with_a_traceback_free_log(monkeypatch, caplog):
    from fastapi import HTTPException

    from routers import ha_control

    async def _boom(path):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(ha_control, "_bridge_get", _boom)
    with caplog.at_level(logging.WARNING, logger="routers.ha_control"):
        for _ in range(3):
            with pytest.raises(HTTPException) as ei:
                asyncio.run(ha_control.list_entities(domain=None, area=None, caller={"user_id": "u"}))
            assert ei.value.status_code == 502
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None


def test_panel_config_ha_outage_returns_none_with_a_traceback_free_log(monkeypatch, caplog):
    from routers import panel_config

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(panel_config.httpx, "AsyncClient", _Client)
    with caplog.at_level(logging.WARNING, logger="routers.panel_config"):
        assert asyncio.run(panel_config._entity_index()) is None
        assert asyncio.run(panel_config._entity_index()) is None
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None


# ── expert trace: INFO, and no reply text ────────────────────────────────────


def test_expert_active_trace_is_info_and_carries_no_reply_text(monkeypatch, caplog):
    import expert_dispatch as ed

    secret_reply = "zz-sentinel-reply-text-zz"

    async def _exec(_intent, _user):
        return secret_reply

    fake = types.ModuleType("intent_router")
    fake.Intent = lambda name, slots: (name, slots)
    fake.execute_intent = _exec
    monkeypatch.setitem(sys.modules, "intent_router", fake)
    monkeypatch.setenv("ZOE_EXPERT_ENABLED", "1")
    monkeypatch.setenv("ZOE_EXPERT_MODE", "active")
    monkeypatch.setattr(ed, "_plan", lambda domain, text: ("time_query", {}, "read"))

    with caplog.at_level(logging.DEBUG, logger="expert_dispatch"):
        result = asyncio.run(ed.dispatch("time", "what time is it", {"score": 0.99, "user_id": "u"}))
    assert result is not None and result.reply == secret_reply
    traces = [r for r in caplog.records if r.getMessage().startswith("EXPERT_ACTIVE")]
    assert len(traces) == 1
    assert traces[0].levelno == logging.INFO
    assert secret_reply not in traces[0].getMessage()
    assert "reply_chars=%d" % len(secret_reply) in traces[0].getMessage()


# ── auth: no credential material in the log ──────────────────────────────────


def test_invalid_session_log_never_contains_the_token(monkeypatch, caplog):
    import auth
    from fastapi import HTTPException

    token = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-token"

    async def _invalid(_sid):
        return None

    monkeypatch.setattr(auth, "_validate_with_auth_service", _invalid)
    monkeypatch.setattr(auth, "_cache_get", lambda _sid: None)

    class _Req:
        headers = {"X-Session-ID": token}

    with caplog.at_level(logging.WARNING, logger="auth"):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(auth.get_current_user(_Req()))
    assert ei.value.status_code == 401
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "Invalid session" in text
    assert token[:8] not in text and token[:20] not in text
    assert auth._session_digest(token) in text


# ── main.py wiring (main is not importable in the slim CI lane: pin by AST) ───


def test_ws_origin_rejection_goes_through_the_throttle():
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_enforce_ws_origin"
    )
    calls = [c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
    attr_calls = [c.func.attr for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]
    assert "log_throttled" in calls
    assert "warning" not in attr_calls  # a bare logger.warning here would reopen the flood
