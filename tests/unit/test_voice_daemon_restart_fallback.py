"""Panel daemon rides out a zoe-data restart: "One moment." + one retry, then a spoken apology.

Before: a 502 / refused connection during a deploy restart got ~1 s of generic retries (and
the generic loop also re-POSTed a 500, which may have already executed a write), then
"Zoe is not available right now". Now the blocking turn makes ONE attempt, and only a
restart-shaped failure (HTTP 502 before a body, or a connect failure - the request provably
never ran) is retried, once, after a "One moment." Real HTTP against a local fake server;
the local TTS is recorded, never spoken.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

DAEMON = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"


@pytest.fixture(scope="module")
def daemon():
    pytest.importorskip("requests")
    pytest.importorskip("numpy")
    fake = types.ModuleType("pyaudio")
    fake.paInt16 = 8
    fake.PyAudio = object
    saved = sys.modules.get("pyaudio")
    sys.modules["pyaudio"] = fake
    try:
        spec = importlib.util.spec_from_file_location("zoe_voice_daemon_restart_fb", DAEMON)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            sys.modules.pop("pyaudio", None)
        else:
            sys.modules["pyaudio"] = saved
    return mod


class _Server:
    """Scripted fake zoe-data: pops (status, body) per POST."""

    def __init__(self, script):
        outer = self
        self.script = list(script)
        self.hits = 0

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.hits += 1
                status, body = outer.script.pop(0) if outer.script else (200, {"ok": True})
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def spoken(daemon, monkeypatch):
    said = []
    monkeypatch.setattr(daemon, "_espeak_local", lambda text: said.append(text))
    monkeypatch.setattr(daemon, "VOICE_TURN_RESTART_RETRY_S", 0.0)
    monkeypatch.setattr(daemon, "_HTTP_SESSION", None)
    return said


def _turn(daemon):
    return daemon._do_single_turn(None, b"RIFFxxxx", prompt_on_empty=False, voice_claim=None)


def _refused_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def test_502_then_recovery_says_one_moment_and_retries_once(daemon, spoken, monkeypatch):
    srv = _Server([(502, {"detail": "bad gateway"}), (200, {"ok": True, "text": "hello", "reply": "hi"})])
    try:
        monkeypatch.setattr(daemon, "ZOE_URL", srv.url)
        _turn(daemon)
        assert srv.hits == 2
        assert spoken == [daemon._FALLBACK_ONE_MOMENT]
    finally:
        srv.close()


def test_502_twice_says_one_moment_then_apology_after_exactly_one_retry(daemon, spoken, monkeypatch):
    srv = _Server([(502, {}), (502, {}), (502, {})])
    try:
        monkeypatch.setattr(daemon, "ZOE_URL", srv.url)
        assert _turn(daemon) is False
        assert srv.hits == 2  # one attempt + ONE retry, never more
        assert spoken == [daemon._FALLBACK_ONE_MOMENT, daemon._FALLBACK_UNREACHABLE]
    finally:
        srv.close()


def test_connect_refused_same_flow(daemon, spoken, monkeypatch):
    monkeypatch.setattr(daemon, "ZOE_URL", _refused_url())
    attempts = []
    real = daemon._api_post
    monkeypatch.setattr(daemon, "_api_post", lambda *a, **k: attempts.append(a[0]) or real(*a, **k))
    assert _turn(daemon) is False
    assert attempts == ["/api/voice/turn", "/api/voice/turn"]  # one attempt + exactly ONE retry
    assert spoken == [daemon._FALLBACK_ONE_MOMENT, daemon._FALLBACK_UNREACHABLE]


def test_200_is_unchanged_and_silent(daemon, spoken, monkeypatch):
    srv = _Server([(200, {"ok": True, "text": "hello", "reply": "hi"})])
    try:
        monkeypatch.setattr(daemon, "ZOE_URL", srv.url)
        _turn(daemon)
        assert srv.hits == 1
        assert spoken == []
    finally:
        srv.close()


def test_500_with_body_is_not_retried(daemon, spoken, monkeypatch):
    """A 500 may have executed a write: no retry, no 'one moment' - straight to the apology."""
    srv = _Server([(500, {"detail": "boom"}), (200, {"ok": True})])
    try:
        monkeypatch.setattr(daemon, "ZOE_URL", srv.url)
        assert _turn(daemon) is False
        assert srv.hits == 1
        assert spoken == [daemon._FALLBACK_UNREACHABLE]
    finally:
        srv.close()


@pytest.mark.parametrize("status", [503, 504])
def test_503_504_are_not_retried(daemon, spoken, monkeypatch, status):
    srv = _Server([(status, {}), (200, {"ok": True})])
    try:
        monkeypatch.setattr(daemon, "ZOE_URL", srv.url)
        assert _turn(daemon) is False
        assert srv.hits == 1
        assert spoken == [daemon._FALLBACK_UNREACHABLE]
    finally:
        srv.close()


def test_dropped_connection_mid_request_is_not_a_connect_failure(daemon):
    import requests

    assert not daemon._is_connect_failure(requests.exceptions.ConnectionError("Connection aborted."))
    assert not daemon._is_connect_failure(requests.exceptions.ReadTimeout("Read timed out."))
    assert daemon._is_connect_failure(requests.exceptions.ConnectTimeout("t"))


def test_on_restart_hook_runs_before_one_moment(daemon, spoken, monkeypatch):
    """The buffer-phrase coordination hook fires before the local announcement, only on a restart."""
    order = []
    monkeypatch.setattr(daemon, "VOICE_TURN_RESTART_RETRY_S", 0.0)
    monkeypatch.setattr(daemon, "_espeak_local", lambda t: order.append(("say", t)))
    srv = _Server([(502, {}), (200, {"ok": True})])
    try:
        monkeypatch.setattr(daemon, "ZOE_URL", srv.url)
        daemon._post_turn_riding_restart({}, lambda: order.append("hook"))
        assert order == ["hook", ("say", daemon._FALLBACK_ONE_MOMENT)]
        order.clear()
        srv.script[:] = [(200, {"ok": True})]
        daemon._post_turn_riding_restart({}, lambda: order.append("hook"))
        assert order == []
    finally:
        srv.close()
