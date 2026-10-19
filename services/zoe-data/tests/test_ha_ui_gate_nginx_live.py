"""The Home Assistant gate through the REAL nginx (opt-in: ``ZOE_NGINX_LIVE_TEST=1`` + docker; skipped everywhere else).

test_ha_ui_gate.py proves the gate's decisions; this proves the wiring nginx -> auth_request -> zoe-data -> proxy. It runs the
repo's real ``nginx.conf`` + ``nginx.d/`` (only the upstream PORTS are rewritten, so it can sit beside the live stack) in a
throwaway container, with the real ``routers/ha_ui`` router as the gate and a stub standing in for Home Assistant (HTTP, plus a
bare WebSocket 101 handshake on ``/api/websocket``). Three nginx instances:

* the real conf - anonymous refused (HTTP, HA's auth flow and the WebSocket), member / panel / cookie admitted, and HA receives
  the path with ``/ha`` stripped (``/ha/auth/providers`` -> ``/auth/providers``) exactly as before;
* a NEGATIVE CONTROL with the ``auth_request`` line removed - an anonymous caller must get straight through, or the test above
  could not tell a gate from no gate;
* the real conf with the gate unreachable - must fail closed (5xx), never open.

Run: ``ZOE_NGINX_LIVE_TEST=1 flock -x /tmp/zoe-test-lane.lock python3 -m pytest services/zoe-data/tests/test_ha_ui_gate_nginx_live.py``
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI

import routers.ha_ui as ha_ui
from test_ha_ui_gate import ACTIVE_PANELS, GUEST_SID, MEMBER_SID, PANEL_IP, _env  # noqa: F401  (autouse: faked auth + registry)

_LIVE = os.environ.get("ZOE_NGINX_LIVE_TEST") == "1" and shutil.which("docker") is not None
pytestmark = pytest.mark.skipif(not _LIVE, reason="set ZOE_NGINX_LIVE_TEST=1 (needs docker + an nginx image)")

NGINX_IMAGE = os.environ.get("ZOE_NGINX_TEST_IMAGE", "nginx:alpine")
UI = Path(__file__).resolve().parents[2] / "zoe-ui"
GATE_PORT, HA_PORT = 18010, 18123
HA = "/ha/"


def _wait_listening(port, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"nothing listening on {port}")


class _HaStub:
    """Stands in for Home Assistant: GET -> 'HA-STUB', Upgrade -> a 101 handshake. Records every path it receives."""

    def __init__(self):
        import http.server

        hits = self.hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self):
                hits.append(self.path)
                if self.headers.get("Upgrade", "").lower() == "websocket":
                    guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                    accept = base64.b64encode(hashlib.sha1((self.headers["Sec-WebSocket-Key"] + guid).encode()).digest()).decode()
                    self.send_response(101)
                    self.send_header("Upgrade", "websocket")
                    self.send_header("Connection", "Upgrade")
                    self.send_header("Sec-WebSocket-Accept", accept)
                    self.end_headers()
                    return
                body = b"HA-STUB"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _answer
            do_POST = _answer

        self.server = http.server.ThreadingHTTPServer(("0.0.0.0", HA_PORT), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class _Gate:
    """The real gate router on GATE_PORT, recording the X-Real-IP nginx hands it."""

    def __init__(self):
        import uvicorn

        app = FastAPI()
        app.include_router(ha_ui.router)
        seen = self.seen_real_ips = []

        @app.middleware("http")
        async def _record(request, call_next):
            seen.append(request.headers.get("x-real-ip", ""))
            return await call_next(request)

        self.server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=GATE_PORT, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        _wait_listening(GATE_PORT)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _conf_dir(root: Path, *, auth_request=True, gate_port=GATE_PORT) -> Path:
    shutil.copytree(UI / "nginx.d", root / "nginx.d")
    shutil.copy(UI / "nginx.conf", root / "default.conf")
    loc = root / "nginx.d" / "locations.inc"
    text = loc.read_text()
    assert "host.docker.internal:8000" in text and "host.docker.internal:8123" in text
    text = text.replace("host.docker.internal:8000", f"host.docker.internal:{gate_port}")
    text = text.replace("host.docker.internal:8123", f"host.docker.internal:{HA_PORT}")
    if not auth_request:
        pattern = re.compile(r"^\s*auth_request\s+/_zoe/ha-gate;\s*$", re.M)
        assert pattern.search(text), "the real conf lost its auth_request"
        text = pattern.sub("", text)
    loc.write_text(text)
    ssl = root / "ssl"
    ssl.mkdir()
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=zoe",
                    "-keyout", str(ssl / "zoe.key"), "-out", str(ssl / "zoe.crt")], check=True, capture_output=True)
    for p in ssl.iterdir():
        p.chmod(0o644)
    return root


class _Nginx:
    def __init__(self, conf: Path, port: int):
        self.name, self.port = f"zoe-ha-gate-test-{port}", port
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        out = subprocess.run(
            ["docker", "run", "-d", "--name", self.name, "--add-host", "zoe-auth:127.0.0.1",
             "--add-host", "host.docker.internal:host-gateway", "-p", f"127.0.0.1:{port}:80",
             "-v", f"{conf}/default.conf:/etc/nginx/conf.d/default.conf:ro", "-v", f"{conf}/nginx.d:/etc/nginx/zoe:ro",
             "-v", f"{conf}/ssl:/etc/nginx/ssl:ro", NGINX_IMAGE],
            capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        _wait_listening(port)

    def stop(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)

    def get(self, path, headers=None, cookie=None):
        import httpx

        h = dict(headers or {})
        if cookie:
            h["Cookie"] = f"{ha_ui.GATE.cookie}={cookie}"
        return httpx.get(f"http://127.0.0.1:{self.port}{path}", headers=h, timeout=10)

    def post(self, path, headers=None, cookie=None):
        import httpx

        h = dict(headers or {})
        if cookie:
            h["Cookie"] = f"{ha_ui.GATE.cookie}={cookie}"
        return httpx.post(f"http://127.0.0.1:{self.port}{path}", headers=h, timeout=10)

    def ws_status(self, path, headers=None, cookie=None) -> str:
        h = {"Host": "zoe.local", "Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Version": "13",
             "Sec-WebSocket-Key": base64.b64encode(b"0123456789abcdef").decode(), **(headers or {})}
        if cookie:
            h["Cookie"] = f"{ha_ui.GATE.cookie}={cookie}"
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall((f"GET {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n").encode())
            return s.recv(4096).split(b"\r\n", 1)[0].decode()


@pytest.fixture
def stack():
    tmp = Path(tempfile.mkdtemp(prefix="zoe-ha-gate-"))
    tmp.chmod(0o755)
    stub, gate, nginx = _HaStub(), _Gate(), []
    try:
        yield type("Stack", (), dict(tmp=tmp, stub=stub, gate=gate, nginx=nginx))
    finally:
        for n in nginx:
            n.stop()
        gate.stop()
        stub.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def _start(stack, port, **kw) -> _Nginx:
    d = stack.tmp / f"c{port}"
    d.mkdir()
    n = _Nginx(_conf_dir(d, **kw), port)
    stack.nginx.append(n)
    return n


def test_real_nginx_refuses_anonymous_and_admits_member_panel_and_cookie(stack):
    nginx = _start(stack, 18090)
    # anonymous / forged: refused, and Home Assistant never saw the request
    assert nginx.get(HA).status_code == 401
    assert nginx.get(HA + "auth/providers").status_code == 401                    # HA's login flow
    assert nginx.post(HA + "auth/login_flow").status_code == 401
    assert nginx.post(HA + "api/webhook/some-id").status_code == 401              # HA webhooks are unauthenticated in HA
    assert nginx.get(HA, {"X-Session-ID": GUEST_SID}).status_code == 401
    assert nginx.get(HA, {"X-Real-IP": PANEL_IP}).status_code == 401              # nginx overwrites a client X-Real-IP
    assert nginx.ws_status(HA + "api/websocket").startswith("HTTP/1.1 401")
    assert nginx.get("/_zoe/ha-gate").status_code == 404                           # `internal`
    assert stack.stub.hits == []
    # a member's header passes (the control for every refusal above), and HA gets the path with /ha stripped
    ok = nginx.get(HA + "auth/providers", {"X-Session-ID": MEMBER_SID})
    assert ok.status_code == 200 and ok.text == "HA-STUB"
    assert stack.stub.hits == ["/auth/providers"]
    # the panel: its address as nginx sees it, once registered, passes - the gate saw nginx's value, never the forged one
    seen = {i for i in stack.gate.seen_real_ips if i and i != PANEL_IP}
    assert seen and PANEL_IP not in stack.gate.seen_real_ips
    ACTIVE_PANELS.update(seen)
    try:
        assert nginx.get(HA).status_code == 200
        assert nginx.ws_status(HA + "api/websocket").startswith("HTTP/1.1 101")
    finally:
        ACTIVE_PANELS.difference_update(seen)
    assert nginx.get(HA).status_code == 401                                        # panel deregistered again
    # the cookie: minted through nginx with a member's header, then carried by HA's WebSocket handshake and its auth flow
    minted = nginx.post("/api/ha-ui/session", {"X-Session-ID": MEMBER_SID})
    assert minted.status_code == 200
    cookie = minted.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    assert nginx.ws_status(HA + "api/websocket", cookie=cookie).startswith("HTTP/1.1 101")
    assert nginx.post(HA + "auth/login_flow", cookie=cookie).text == "HA-STUB"
    assert nginx.get(HA, cookie=cookie[:-2] + "xx").status_code == 401
    assert nginx.ws_status(HA + "api/websocket", cookie=cookie[:-2] + "xx").startswith("HTTP/1.1 401")
    assert nginx.post("/api/ha-ui/session").status_code == 403                     # no credentials, no cookie
    assert "/api/websocket" in stack.stub.hits and "/auth/login_flow" in stack.stub.hits


def test_negative_control_without_the_auth_request_line_the_same_requests_get_through(stack):
    nginx = _start(stack, 18091, auth_request=False)
    assert nginx.get(HA + "auth/providers").text == "HA-STUB"                      # exactly what the old conf allowed
    assert nginx.ws_status(HA + "api/websocket").startswith("HTTP/1.1 101")


def test_the_gate_failing_to_answer_fails_closed(stack):
    nginx = _start(stack, 18092, gate_port=18999)                                  # nothing listens there
    assert nginx.get(HA, {"X-Session-ID": MEMBER_SID}).status_code >= 500
    assert nginx.ws_status(HA + "api/websocket", {"X-Session-ID": MEMBER_SID}).split()[1].startswith("5")
    assert stack.stub.hits == []
