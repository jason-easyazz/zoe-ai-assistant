"""Relay ingress against a REAL nginx (the shipped ``nginx.conf`` + ``nginx.d/*`` + ``relay-listener.conf``) in front of the REAL
zoe-data auth code - the "lab" cells T5 / T10 / T11 and the fingerprint binding with nothing faked on the nginx side.

NOT ``ci_safe``: it starts a throwaway ``nginx:alpine`` container and a uvicorn server on the docker bridge, so it runs only when
asked for (``ZOE_RELAY_NGINX_TEST=1``), only with docker + the host ``openssl``, and takes ~15 s and ~20 MB. The unit-level pins
(text of the config, the rules in Python) are the ``ci_safe`` tests ``tests/unit/test_relay_nginx_listener.py`` and
``test_relay_ingress.py``; this file is the check that those pins describe what nginx actually does:

  * the listener needs a certificate from the HOUSEHOLD CA: none, or one from another CA, reaches zoe-data never (hit counter);
  * ``X-Zoe-Ingress`` / ``X-Zoe-Client-Cert-Fp`` sent by the client are OVERWRITTEN on the relay listener and DROPPED on the LAN
    block; ``X-Forwarded-For`` / ``X-Real-IP`` are not forwarded on the relay;
  * a request through the relay without a device credential is 401, ``POST /api/ha/control`` is refused (and the same request on
    the LAN listener behaves per #2089);
  * a bound token over the relay is the panel principal only; a member route needs a fresh sign-in;
  * a certificate bound to panel A with panel B's token is refused;
  * Music Assistant / Home Assistant UI / Multica / LiveKit signalling 404 over the relay.

Run:  ZOE_RELAY_NGINX_TEST=1 flock -x -w 3600 /tmp/zoe-test-lane.lock nice -n 10 pytest services/zoe-data/tests/test_relay_nginx_live.py
(never between 01:50 and 04:55, and only with MemAvailable >= 1200 MB). Invented data; throwaway PKI in a tmp dir.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi import Request

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pytestmark = pytest.mark.skipif(
    os.environ.get("ZOE_RELAY_NGINX_TEST") != "1" or shutil.which("docker") is None or shutil.which("openssl") is None,
    reason="opt-in: set ZOE_RELAY_NGINX_TEST=1 (needs docker + openssl)",
)

import auth  # noqa: E402
import test_relay_ingress as t  # noqa: E402  (its helpers; its autouse fixtures are re-exported below)
from routers import ha_control  # noqa: E402

# Fixtures from the unit module (pytest collects imported fixtures by name).
from test_relay_ingress import _env, reg, on, bridge  # noqa: E402,F401

HID = "h0a1b2c3d4"
NAME = HID + ".zoe.internal"
IMAGE = os.environ.get("ZOE_RELAY_NGINX_IMAGE", "nginx:alpine")


def _sh(*args, check=True):
    return subprocess.run(list(args), check=check, capture_output=True, text=True)


def _load_script(name):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, REPO / "scripts/setup/relay" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Counting:
    """Wraps the ASGI app: every scope that reaches zoe-data is recorded (path + the ingress headers it carried)."""

    def __init__(self, app):
        self.app = app
        self.seen = []

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            hdrs = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            self.seen.append({"path": scope["path"], "headers": hdrs, "peer": (scope.get("client") or ["?"])[0]})
        await self.app(scope, receive, send)


@pytest.fixture
def stack(tmp_path, reg, on, bridge, monkeypatch):
    """throwaway PKI + a real zoe-data auth stack on the docker bridge + a real nginx container wired to it."""
    import uvicorn

    pki = _load_script("household_pki")
    enable = _load_script("enable_relay_listener")
    ca_dir, ssl_dir = tmp_path / "ca", tmp_path / "ssl"
    relay_dir = ssl_dir / "relay"
    pki.init_ca(ca_dir)
    pki.issue_leaf(HID, ca_dir, relay_dir)
    # a self-signed cert for the LAN :443 server block (the shipped conf requires one)
    _sh("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-keyout",
        str(ssl_dir / "zoe.key"), "-out", str(ssl_dir / "zoe.crt"), "-days", "2", "-subj", "/CN=lan")

    def panel_cert(name, signer_dir):
        key, csr, crt = tmp_path / (name + ".key"), tmp_path / (name + ".csr"), tmp_path / (name + ".pem")
        _sh("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key))
        _sh("openssl", "req", "-new", "-key", str(key), "-subj", "/CN=" + name, "-out", str(csr))
        fp = pki.sign_csr(csr, name, signer_dir, crt)
        return str(crt), str(key), fp

    kitchen = panel_cert("kitchen-panel", ca_dir)
    hall = panel_cert("hall-panel", ca_dir)
    rogue_ca = tmp_path / "rogue-ca"
    pki.init_ca(rogue_ca)
    rogue = panel_cert("rogue-panel", rogue_ca)

    # The shipped enable script installs the listener (the real path), with the flag check on a real env file.
    # (the env-file flag / trusted-peer checks are unit-tested; the peer here is the container this fixture starts, known only later)
    assert enable.main(["--repo", str(REPO), "--ssl-dir", str(ssl_dir), "--skip-flag-check"]) == 0

    reg.cert(kitchen[2], "kitchen-panel")
    reg.cert(hall[2], "hall-panel")

    gw = _sh("docker", "network", "inspect", "bridge", "-f", "{{(index .IPAM.Config 0).Gateway}}").stdout.strip()
    with socket.socket() as s:
        s.bind((gw, 0))
        app_port = s.getsockname()[1]

    app = t.make_app(reg)

    @app.get("/api/echo-headers")
    async def echo(request: Request):
        return {k: request.headers.get(k) for k in ("x-zoe-ingress", "x-zoe-client-cert-fp", "x-forwarded-for", "x-real-ip")}

    counting = _Counting(app)
    server = uvicorn.Server(uvicorn.Config(counting, host=gw, port=app_port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started

    # nginx.d with ONLY the zoe-data upstream port swapped for the test app's (everything else is the shipped file).
    nginx_d = tmp_path / "nginx.d"
    shutil.copytree(REPO / "services/zoe-ui/nginx.d", nginx_d)
    loc = nginx_d / "locations.inc"
    loc.write_text(loc.read_text().replace("host.docker.internal:8000", "host.docker.internal:%d" % app_port))
    listener = relay_dir / "listener.conf"  # the auth gate subrequest also targets zoe-data's port
    listener.write_text(listener.read_text().replace("host.docker.internal:8000", "host.docker.internal:%d" % app_port))
    name = "zoe-relay-live-" + uuid.uuid4().hex[:8]
    _sh("docker", "run", "-d", "--rm", "--name", name,
        "--add-host", "host.docker.internal:host-gateway", "--add-host", "zoe-auth:127.0.0.1",
        "-v", "%s:/etc/nginx/conf.d/default.conf:ro" % (REPO / "services/zoe-ui/nginx.conf"),
        "-v", "%s:/etc/nginx/zoe:ro" % nginx_d, "-v", "%s:/etc/nginx/ssl:ro" % ssl_dir,
        "-p", "127.0.0.1::9443", "-p", "127.0.0.1::80", IMAGE)
    try:
        def port(inner):
            return int(_sh("docker", "port", name, str(inner)).stdout.strip().splitlines()[0].rsplit(":", 1)[1])

        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port(80)), timeout=0.5).close()
                break
            except (OSError, subprocess.CalledProcessError):
                time.sleep(0.25)
        ip = _sh("docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name).stdout.strip()
        monkeypatch.setenv("ZOE_RELAY_TRUSTED_PEER", ip + "/32")  # the container itself: one address, not the docker /12
        yield dict(relay=port(9443), nginx_ip=ip, lan=port(80), ca=str(relay_dir / "ca.pem"), kitchen=kitchen, hall=hall, rogue=rogue,
                   seen=counting.seen, bridge=bridge, reg=reg, gw=gw, container=name)
    finally:
        print(subprocess.run(["docker", "logs", "--tail", "15", name], capture_output=True, text=True).stderr)  # shown on a failure
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        server.should_exit = True
        thread.join(timeout=5)


# ── a tiny client: PROXY protocol v2 (what frpc sends) + TLS 1.3 + optional client certificate ───────────────────────────────


def _proxy_v2(src="198.51.100.7", dst="10.77.0.1", sport=40000, dport=443):
    sig = b"\r\n\r\n\x00\r\nQUIT\n"
    addr = socket.inet_aton(src) + socket.inet_aton(dst) + struct.pack("!HH", sport, dport)
    return sig + bytes([0x21, 0x11]) + struct.pack("!H", len(addr)) + addr


def _read_response(sock):
    buf = b""
    sock.settimeout(8)
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            head, _, body = buf.partition(b"\r\n\r\n")
            if _ and b"content-length" in head.lower():
                need = int([ln for ln in head.split(b"\r\n") if ln.lower().startswith(b"content-length")][0].split(b":")[1])
                if len(body) >= need:
                    break
    except (socket.timeout, ConnectionResetError, ssl.SSLError):
        pass
    head, _, body = buf.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1]) if head.startswith(b"HTTP/") else None
    return status, body.decode("utf-8", "replace")


def relay_get(stack, method, path, headers=None, *, cert=None, ca=None, body=None, tls_max=None):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3 if tls_max is None else ssl.TLSVersion.TLSv1_2
    if tls_max:
        ctx.maximum_version = tls_max
    ctx.load_verify_locations(ca or stack["ca"])
    if cert:
        ctx.load_cert_chain(cert[0], cert[1])
    raw = socket.create_connection(("127.0.0.1", stack["relay"]), timeout=8)
    raw.sendall(_proxy_v2())
    try:
        s = ctx.wrap_socket(raw, server_hostname=NAME)
    except (ssl.SSLError, OSError) as e:
        return ("handshake", str(e))
    hdrs = {"Host": NAME, "Connection": "close", **(headers or {})}
    payload = body.encode() if isinstance(body, str) else (body or b"")
    if payload:
        hdrs["Content-Length"] = str(len(payload))
    req = ("%s %s HTTP/1.1\r\n" % (method, path) + "".join("%s: %s\r\n" % kv for kv in hdrs.items()) + "\r\n").encode() + payload
    try:
        s.sendall(req)
    except (ssl.SSLError, OSError) as e:
        return ("dropped", str(e))
    status, text = _read_response(s)
    s.close()
    if status is None:
        return ("dropped", text)
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def lan_get(stack, method, path, headers=None, body=None):
    import http.client

    c = http.client.HTTPConnection("127.0.0.1", stack["lan"], timeout=8)
    h = dict(headers or {})
    if body is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    text = r.read().decode()
    c.close()
    try:
        return r.status, json.loads(text)
    except ValueError:
        return r.status, text


def creds(stack, who="kitchen", token=None, **extra):
    h = {"X-Device-Token": token if token is not None else {"kitchen": t.TOK_KITCHEN, "hall": t.TOK_HALL}[who]}
    h.update(extra)
    return h


# ── T5: nginx overwrites / drops the headers ───────────────────────────────────────────────────────────────────────────────


def test_t5_nginx_overwrites_the_ingress_headers_on_the_relay_and_forwards_no_address(stack):
    forged = {"X-Zoe-Ingress": "lan", "X-Zoe-Client-Cert-Fp": "f" * 40, "X-Forwarded-For": "192.0.2.50", "X-Real-IP": "192.0.2.50"}
    status, body = relay_get(stack, "GET", "/api/echo-headers", {**forged, **creds(stack)}, cert=stack["kitchen"][:2])
    assert status == 200, body
    assert body["x-zoe-ingress"] == "relay", "a client-sent 'lan' must be overwritten"
    assert body["x-zoe-client-cert-fp"] == stack["kitchen"][2], "the fingerprint is the VERIFIED certificate's, not the forged one"
    assert body["x-forwarded-for"] is None and body["x-real-ip"] is None, "a relayed request carries no address"
    # Positive control for the probe: the LAN block forwards the address headers (and still ends them with the real peer).
    lstatus, lbody = lan_get(stack, "GET", "/api/echo-headers", {"X-Forwarded-For": "192.0.2.50"})
    assert lstatus == 200 and lbody["x-real-ip"] and lbody["x-forwarded-for"].startswith("192.0.2.50, ")


def test_t5_the_lan_block_drops_the_ingress_headers_a_client_writes(stack):
    claim = {"X-Zoe-Ingress": "relay", "X-Zoe-Client-Cert-Fp": stack["kitchen"][2], **creds(stack)}
    status, body = lan_get(stack, "GET", "/api/echo-headers", claim)
    assert status == 200
    assert body["x-zoe-ingress"] is None and body["x-zoe-client-cert-fp"] is None, "a LAN client can never claim the relay"
    # ... so zoe-data judges it as the LAN: the device token is the panel, no relay marker, and a credential-less claim is a
    # plain guest (200), not the relay's 401.
    status, who = lan_get(stack, "GET", "/api/whoami", claim)
    assert status == 200 and who["user"]["panel_id"] == "kitchen-panel" and "ingress" not in who["user"]
    status, who = lan_get(stack, "GET", "/api/whoami", {"X-Zoe-Ingress": "relay", "X-Zoe-Client-Cert-Fp": stack["kitchen"][2]})
    assert status == 200 and who["user"]["user_id"] == "guest"


def test_t5_a_relayed_guest_never_matches_the_registered_panel_address(stack):
    stack["reg"].con.execute("UPDATE panels SET ip_address = ? WHERE panel_id = 'kitchen-panel'", (stack["gw"],))
    stack["reg"].con.commit()
    # LAN: the nginx container reaches zoe-data as a trusted proxy and X-Real-IP is the caller (the docker gateway here), which
    # IS the kitchen panel's registered address -> a guest session from it is "the panel".
    lstatus, lan = lan_get(stack, "GET", "/api/whoami", {"X-Session-ID": t.SID_GUEST})
    assert lstatus == 200 and lan["panel_registered"] is True, lan
    # Relay: no address at all, so even with the very same registered address the guest session is NOT the panel (and with no
    # device credential it does not get past the gate).
    status, body = relay_get(stack, "GET", "/api/whoami", {"X-Session-ID": t.SID_GUEST}, cert=stack["kitchen"][:2])
    assert status == 401 and body["detail"] == "relay_device_credential_required"
    status, body = relay_get(stack, "GET", "/api/whoami", creds(stack), cert=stack["kitchen"][:2])
    assert status == 200 and body["client_ip"] == ""


# ── mTLS: nothing without a household certificate reaches zoe-data ────────────────────────────────────────────────────────


def test_no_certificate_or_a_foreign_ca_certificate_gets_nowhere(stack):
    before = len(stack["seen"])
    none = relay_get(stack, "GET", "/api/whoami", creds(stack))
    assert none[0] in (400, 496, "dropped", "handshake"), none  # lab finding 2: nginx answers 400 after the handshake
    rogue = relay_get(stack, "GET", "/api/whoami", creds(stack), cert=stack["rogue"][:2])
    assert rogue[0] in (400, 496, "dropped", "handshake"), rogue
    assert len(stack["seen"]) == before, "zoe-data must not have seen either request"
    # POSITIVE CONTROL (same client code, same path, a household certificate):
    ok = relay_get(stack, "GET", "/api/whoami", creds(stack), cert=stack["kitchen"][:2])
    assert ok[0] == 200 and len(stack["seen"]) == before + 1


def test_a_stale_trusted_peer_setting_is_refused_not_downgraded_to_the_lan(stack, monkeypatch):
    """The nginx container's address changes when it is recreated. With the flag on and ZOE_RELAY_TRUSTED_PEER stale, relayed
    traffic must stop (403), never be judged by the LAN rules; and another address in the same docker /12 is not nginx."""
    ok = relay_get(stack, "GET", "/api/whoami", creds(stack), cert=stack["kitchen"][:2])
    assert ok[0] == 200
    head, tail = stack["nginx_ip"].rsplit(".", 1)
    other = "%s.%d" % (head, 250 if tail != "250" else 249)
    monkeypatch.setenv("ZOE_RELAY_TRUSTED_PEER", other)
    status, body = relay_get(stack, "GET", "/api/whoami", creds(stack), cert=stack["kitchen"][:2])
    assert status == 403 and body["detail"] == "relay_marker_untrusted_peer", (status, body)
    monkeypatch.delenv("ZOE_RELAY_TRUSTED_PEER")
    assert relay_get(stack, "GET", "/api/whoami", creds(stack), cert=stack["kitchen"][:2])[0] == 403  # unset trusts nobody


def test_tls_below_1_3_is_refused(stack):
    r = relay_get(stack, "GET", "/api/whoami", creds(stack), cert=stack["kitchen"][:2], tls_max=ssl.TLSVersion.TLSv1_2)
    assert r[0] == "handshake", r


def test_a_connection_without_proxy_protocol_is_refused(stack):
    raw = socket.create_connection(("127.0.0.1", stack["relay"]), timeout=5)
    raw.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    raw.settimeout(5)
    try:
        data = raw.recv(100)
    except (socket.timeout, ConnectionResetError):
        data = b""
    raw.close()
    assert not data.startswith(b"HTTP/1.1 200")


# ── T10 / T11 / the fingerprint binding through the real stack ─────────────────────────────────────────────────────────────


def test_t10_credentialless_is_401_and_home_control_is_refused_over_the_relay(stack):
    cert = stack["kitchen"][:2]
    body = json.dumps(t.CONTROL)
    status, r = relay_get(stack, "POST", "/api/ha/control", {"Content-Type": "application/json"}, cert=cert, body=body)
    assert status == 401 and r["detail"] == "relay_device_credential_required"
    status, r = relay_get(stack, "POST", "/api/ha/control", {"Content-Type": "application/json", "X-Session-ID": t.SID_GUEST},
                          cert=cert, body=body)
    assert status == 401
    assert stack["bridge"] == []
    # With the device token: the panel principal, which does NOT drive the house over the relay.
    status, r = relay_get(stack, "POST", "/api/ha/control", {"Content-Type": "application/json", **creds(stack)}, cert=cert, body=body)
    assert status == 403 and stack["bridge"] == []
    # The same credential-less request on the LAN listener behaves per #2089 (a plain guest: 403) ...
    lstatus, _ = lan_get(stack, "POST", "/api/ha/control", {"X-Session-ID": t.SID_GUEST}, body=body)
    assert lstatus == 403
    # ... and the LAN panel (device token) may - which proves the route and the bridge fake are live.
    lstatus, _ = lan_get(stack, "POST", "/api/ha/control", creds(stack), body=body)
    assert lstatus == 200 and len(stack["bridge"]) == 1


def test_t11_a_bound_token_is_the_panel_and_a_member_route_needs_a_fresh_sign_in(stack, monkeypatch):
    cert = stack["kitchen"][:2]
    monkeypatch.setenv("ZOE_DEVICE_TOKEN_BINDS_MEMBER", "1")
    lstatus, lan = lan_get(stack, "GET", "/api/whoami", creds(stack))
    assert lstatus == 200 and lan["user"]["user_id"] == "casey", "the binding is real: on the LAN the rollback flag makes the token a member"
    status, relayed = relay_get(stack, "GET", "/api/whoami", creds(stack), cert=cert)
    assert status == 200 and relayed["user"]["user_id"] == "guest" and relayed["user"]["panel_id"] == "kitchen-panel"
    monkeypatch.delenv("ZOE_DEVICE_TOKEN_BINDS_MEMBER")
    status, _ = relay_get(stack, "GET", "/api/member", creds(stack, **{"X-Session-ID": t.SID_CASEY}), cert=cert)
    assert status == 403
    t.FRESH["kitchen-panel"] = "casey"
    status, r = relay_get(stack, "GET", "/api/member", creds(stack, **{"X-Session-ID": t.SID_CASEY}), cert=cert)
    assert status == 200 and r == {"user_id": "casey"}


def test_a_certificate_bound_to_another_panel_with_this_panels_token_is_refused(stack):
    status, r = relay_get(stack, "GET", "/api/whoami", creds(stack, who="kitchen"), cert=stack["hall"][:2])
    assert status == 401 and r["detail"] == "relay_certificate_credential_mismatch"
    ok, _ = relay_get(stack, "GET", "/api/whoami", creds(stack, who="hall"), cert=stack["hall"][:2])
    assert ok == 200
    # revoke the hall certificate: refused at once, and the kitchen's still works
    stack["reg"].con.execute("UPDATE panel_client_certs SET revoked_at = 'now' WHERE fingerprint = ?", (stack["hall"][2],))
    stack["reg"].con.commit()
    t.relay_ingress.invalidate_cache()
    assert relay_get(stack, "GET", "/api/whoami", creds(stack, who="hall"), cert=stack["hall"][:2])[0] == 401
    assert relay_get(stack, "GET", "/api/whoami", creds(stack, who="kitchen"), cert=stack["kitchen"][:2])[0] == 200


# ── what is and is not reachable over the relay ────────────────────────────────────────────────────────────────────────────


def test_services_with_no_zoe_auth_404_over_the_relay_and_livekit_is_answered(stack):
    cert = stack["kitchen"][:2]
    for path in ("/ha/", "/modules/music-assistant/", "/multica/", "/multica-api/x", "/_next/x", "/livekit/"):
        status, _ = relay_get(stack, "GET", path, creds(stack), cert=cert)
        assert status == 404, path
    status, body = relay_get(stack, "GET", "/api/voice/livekit-token", creds(stack), cert=cert)
    assert status == 200 and body == {"token": "", "url": "", "livekit_available": False}
    # The static UI is served by nginx itself over the relay (a certificate is all it takes; no zoe-data hit).
    n = len(stack["seen"])
    status, _ = relay_get(stack, "GET", "/touch/home.html", cert=cert)
    assert status in (200, 404) and len(stack["seen"]) == n


def test_the_websocket_upgrade_passes_the_same_gate_through_nginx(stack):
    def upgrade(headers, cert):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(stack["ca"])
        ctx.load_cert_chain(*cert)
        raw = socket.create_connection(("127.0.0.1", stack["relay"]), timeout=8)
        raw.sendall(_proxy_v2())
        s = ctx.wrap_socket(raw, server_hostname=NAME)
        h = {"Host": NAME, "Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Version": "13",
             "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", **headers}
        s.sendall(("GET /ws/push HTTP/1.1\r\n" + "".join("%s: %s\r\n" % kv for kv in h.items()) + "\r\n").encode())
        status, _ = _read_response(s)
        s.close()
        return status

    cert = stack["kitchen"][:2]
    assert upgrade(creds(stack), cert) == 101
    assert upgrade({}, cert) in (403, 401), "no device credential: refused before the socket is accepted"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl not installed")
def test_http2_with_a_client_certificate_works_over_the_listener(stack):
    """Chromium negotiates h2: the listener's `http2 on` must not break PROXY protocol + mTLS (curl speaks PROXY v1; frpc sends v2)."""
    kitchen = stack["kitchen"]
    url = "https://%s:%d/api/echo-headers" % (NAME, stack["relay"])
    base = ["curl", "-s", "--http2", "--haproxy-protocol", "--resolve", "%s:%d:127.0.0.1" % (NAME, stack["relay"]),
            "--cacert", stack["ca"], "-H", "X-Device-Token: " + t.TOK_KITCHEN, "-w", "\n%{http_version} %{http_code}", url]
    ok = subprocess.run(base + ["--cert", kitchen[0], "--key", kitchen[1]], capture_output=True, text=True, timeout=20)
    body, _, meta = ok.stdout.rpartition("\n")
    assert meta.strip() == "2 200", (ok.stdout, ok.stderr)
    assert json.loads(body)["x-zoe-ingress"] == "relay"
    # NEGATIVE CONTROL: the same request without the client certificate is not a 200.
    bad = subprocess.run(base, capture_output=True, text=True, timeout=20)
    assert not bad.stdout.rstrip().endswith(" 200"), bad.stdout


def test_zoe_auth_routes_are_gated_by_the_certificate_binding_over_the_relay(stack):
    """zoe-auth knows nothing about client certificates: nginx asks zoe-data first (auth_request). In this rig zoe-auth is not
    running, so 'past the gate' is 502 (nginx could not reach it) while 'refused by the gate' is 401."""
    kitchen, hall = stack["kitchen"][:2], stack["hall"][:2]
    for path in ("/api/auth/profiles", "/api/touch-panel/x", "/api/admin/x", "/api/sso/x", "/.well-known/openid", "/jwks.json"):
        assert relay_get(stack, "GET", path, {}, cert=kitchen)[0] == 401, path                     # certificate but no credential
        assert relay_get(stack, "GET", path, creds(stack, who="kitchen"), cert=hall)[0] == 401, path    # another panel's certificate
        status, _ = relay_get(stack, "GET", path, creds(stack), cert=kitchen)
        assert status in (502, 504), (path, status)                                                # POSITIVE: the gate let it through
    stack["reg"].con.execute("UPDATE panel_client_certs SET revoked_at = 'now' WHERE fingerprint = ?", (stack["kitchen"][2],))
    stack["reg"].con.commit()
    t.relay_ingress.invalidate_cache()
    assert relay_get(stack, "GET", "/api/auth/profiles", creds(stack), cert=stack["kitchen"][:2])[0] == 401  # a revoked certificate
    # The LAN is unchanged: the same route is not gated by anything and fails only because zoe-auth is not running here.
    status, _ = lan_get(stack, "GET", "/api/auth/profiles")
    assert status in (502, 504)
