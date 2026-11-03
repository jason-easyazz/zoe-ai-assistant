"""A credential in a URL query string never reaches zoe-data's logs (security fix 2026-10-19).

Browsers cannot set headers on a WebSocket, so the push / lists / voice sockets carry the session id in the query, and uvicorn
logged every handshake with its full query (``"WebSocket /ws/push?channel=all&session_id=<id>" [accepted]``), and a session id
is a bearer credential. ``log_redaction.QuerySecretFilter`` on ``uvicorn.error`` + ``uvicorn.access``
rewrites those values to ``<redacted>``. Tests use uvicorn's REAL record shapes and its REAL ``AccessFormatter``.

Negative control (in this file): the same record through a logger WITHOUT the filter still carries the secret.
"""
from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import log_redaction  # noqa: E402
import logging_setup  # noqa: E402

pytestmark = pytest.mark.ci_safe

SECRET = "sid-0123456789abcdef-live-credential"


def _capture(name: str, fmt: logging.Formatter | None = None):
    logger = logging.getLogger(name)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(fmt or logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    old_level, old_prop = logger.level, logger.propagate
    had_filter = log_redaction._FILTER in logger.filters   # another test (configure_logging) may have installed it
    logger.removeFilter(log_redaction._FILTER)              # every test starts from "not installed"
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, stream, (handler, old_level, old_prop, had_filter)


def _release(logger, saved):
    handler, level, prop, had_filter = saved
    logger.removeHandler(handler)
    logger.setLevel(level)
    logger.propagate = prop
    logger.removeFilter(log_redaction._FILTER)
    if had_filter:
        logger.addFilter(log_redaction._FILTER)


def test_websocket_handshake_line_loses_the_session_id_but_keeps_the_rest():
    logger, out, saved = _capture("uvicorn.error")
    try:
        log_redaction.install()
        # uvicorn.protocols.websockets.*_impl: logger.info('%s - "WebSocket %s" [accepted]', client, path_with_query)
        logger.info('%s - "WebSocket %s" [accepted]', "172.18.0.3:59270", f"/ws/push?channel=all&session_id={SECRET}")
        logger.info('%s - "WebSocket %s" [accepted]', "172.18.0.3:59271", f"/ws/voice/?session_id={SECRET}")
    finally:
        _release(logger, saved)
    text = out.getvalue()
    assert SECRET not in text
    assert '"WebSocket /ws/push?channel=all&session_id=<redacted>" [accepted]' in text
    assert '"WebSocket /ws/voice/?session_id=<redacted>" [accepted]' in text


def test_access_lines_keep_their_shape_for_uvicorns_access_formatter():
    """``AccessFormatter`` unpacks ``record.args`` positionally: the filter must redact IN the tuple, not collapse it."""
    uv_logging = pytest.importorskip("uvicorn.logging")
    fmt = uv_logging.AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    logger, out, saved = _capture("uvicorn.access", fmt)
    try:
        log_redaction.install()
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5", "GET",
                    f"/api/music/setup/browser/status?session_id={SECRET}&limit=5", "1.1", 200)
    finally:
        _release(logger, saved)
    text = out.getvalue()
    assert SECRET not in text
    assert "GET /api/music/setup/browser/status?session_id=<redacted>&limit=5 HTTP/1.1" in text and "200" in text


def test_negative_control_without_the_filter_the_secret_is_logged():
    logger, out, saved = _capture("uvicorn.error")
    try:
        logger.info('%s - "WebSocket %s" [accepted]', "172.18.0.3:1", f"/ws/push?channel=all&session_id={SECRET}")
    finally:
        _release(logger, saved)
    assert SECRET in out.getvalue()


@pytest.mark.parametrize("raw, clean", [
    (f"/x?token={SECRET}", "/x?token=<redacted>"),
    (f"/x?a=1&Session_Id={SECRET}&b=2", "/x?a=1&Session_Id=<redacted>&b=2"),
    (f"/x?device_token={SECRET}#frag", "/x?device_token=<redacted>#frag"),
    ("/x?channel=all&limit=5", "/x?channel=all&limit=5"),           # nothing credential-named: untouched
    ("/x?mysession_id=keep", "/x?mysession_id=keep"),                 # name-anchored, not a substring match
])
def test_redact_query_secrets(raw, clean):
    assert log_redaction.redact_query_secrets(raw) == clean


def test_configure_logging_installs_it_idempotently():
    access, err = logging.getLogger("uvicorn.access"), logging.getLogger("uvicorn.error")
    access.removeFilter(log_redaction._FILTER)
    err.removeFilter(log_redaction._FILTER)
    try:
        logging_setup.install_query_secret_filter()
        logging_setup.install_query_secret_filter()
        assert access.filters.count(log_redaction._FILTER) == 1
        assert err.filters.count(log_redaction._FILTER) == 1
    finally:
        access.removeFilter(log_redaction._FILTER)
        err.removeFilter(log_redaction._FILTER)
