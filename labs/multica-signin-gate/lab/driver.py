"""The lab's client: runs INSIDE the lab network (stdlib only) and talks to the lab nginx exactly as a browser on the LAN talks
to zoe-ui. One phase per invocation; prints one JSON object of raw observations. Judging is lab.py's job.

    python3 driver.py main      # every cell against the real conf
    python3 driver.py gatedown  # the real conf with the gate container stopped
    python3 driver.py control   # the NEGATIVE CONTROL nginx: the same conf with every board auth_request removed
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import sys
import time

NGINX = os.environ.get("LAB_NGINX", "nginx")
SECRET = os.environ["LAB_GATE_SECRET"]
DEV_CODE = os.environ["LAB_DEV_CODE"]
EMAIL = os.environ["LAB_EMAIL"]
COOKIE = "zoe_multica_ui"


def req(method, path, headers=None, body=None, host=None):
    conn = http.client.HTTPConnection(host or NGINX, 80, timeout=20)
    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers or {})
    if data is not None:
        h.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    raw = r.read()
    out = {"status": r.status, "location": r.getheader("Location", ""),
           "set_cookie": r.headers.get_all("Set-Cookie") or [],
           "nginx_error_page": b"<center>nginx" in raw,  # the body nginx itself writes for a refusal / 5xx
           "body": raw[:4000].decode("utf-8", "replace")}
    conn.close()
    return out


def ws_handshake(path, headers=None, host=None):
    """A bare RFC 6455 handshake; returns the status line's code (101 = upgraded) and whether nginx wrote the answer."""
    s = socket.create_connection((host or NGINX, 80), timeout=20)
    key = base64.b64encode(secrets.token_bytes(16)).decode()
    lines = [f"GET {path} HTTP/1.1", f"Host: {host or NGINX}", "Upgrade: websocket", "Connection: Upgrade",
             f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13", "Origin: http://zoe.local"]
    lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
    s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    try:
        buf += s.recv(4096) if b"<center>nginx" not in buf and not buf.startswith(b"HTTP/1.1 101") else b""
    except OSError:
        pass
    s.close()
    m = re.match(rb"HTTP/1\.1 (\d{3})", buf)
    return {"status": int(m.group(1)) if m else 0, "nginx_error_page": b"<center>nginx" in buf}


def forge(claims, key, ttl=600):
    """The gate's token format (auth_handoff.SignedTokens), re-implemented so a cell can sign with ANY key. A token signed with
    the lab's real secret and the right purpose must PASS (positive control), so a refusal of the others means the key /
    purpose / expiry was checked, not that the format was wrong."""
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    payload = {**claims, "exp": int(time.time()) + ttl, "n": secrets.token_urlsafe(9)}
    body = b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{b64(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())}"


def ck(token):
    return {"Cookie": f"{COOKIE}={token}"}


def cookie_from(set_cookies):
    for c in set_cookies:
        name, _, rest = c.partition("=")
        if name.strip() == COOKIE:
            return rest.split(";", 1)[0]
    return ""


def asset_path(html):
    m = re.search(r'(/_next/static/[^"\'\s>]+\.(?:js|css))', html)
    return m.group(1) if m else ""


def multica_login(extra):
    """Multica's OWN login (the dev code) - the second step. Returns its observations."""
    sent = req("POST", "/multica-api/auth/send-code", extra, {"email": EMAIL})
    ver = req("POST", "/multica-api/auth/verify-code", extra, {"email": EMAIL, "code": DEV_CODE})
    auth = [c.split(";", 1)[0] for c in ver["set_cookie"] if c.startswith("multica_auth=")]
    token = ""
    try:
        token = json.loads(ver["body"]).get("token", "")
    except ValueError:
        pass
    me = req("GET", "/multica-api/api/me", {**extra, "Cookie": "; ".join(filter(None, [extra.get("Cookie", ""), *auth])),
                                           **({"Authorization": f"Bearer {token}"} if token else {})})
    return {"send_code": sent, "verify_code": ver, "me": me, "got_multica_session": bool(auth or token)}


def phase_main():
    o = {}
    # -- anonymous: nothing of the board, and Multica's code login is unreachable --------------------------------------
    o["anon_page"] = req("GET", "/multica/")
    o["anon_api_health"] = req("GET", "/multica-api/health")
    o["anon_asset"] = req("GET", "/_next/static/chunks/main.js")
    o["anon_ws"] = ws_handshake("/multica-api/ws")
    o["anon_login"] = multica_login({})
    o["gate_direct"] = req("GET", "/_zoe/multica-gate")
    o["launcher_page"] = req("GET", "/board.html")
    # -- an adult member: mint the cookie the way board.html does, then the board works -------------------------------
    o["member_mint"] = req("POST", "/api/multica-ui/session", {"X-Session-ID": "sid-ada"}, {})
    tok = cookie_from(o["member_mint"]["set_cookie"])
    o["member_page"] = req("GET", "/multica/", ck(tok))
    o["member_asset_path"] = asset_path(o["member_page"]["body"])
    o["member_asset"] = req("GET", o["member_asset_path"], ck(tok)) if o["member_asset_path"] else {"status": 0}
    o["member_api_health"] = req("GET", "/multica-api/health", ck(tok))
    o["member_login"] = multica_login(ck(tok))
    auth = [c.split(";", 1)[0] for c in o["member_login"]["verify_code"]["set_cookie"] if c.startswith("multica_auth=")]
    o["member_ws"] = ws_handshake("/multica-api/ws", {"Cookie": "; ".join([f"{COOKIE}={tok}", *auth])})
    o["member_header_only"] = req("GET", "/multica-api/health", {"X-Session-ID": "sid-ada"})
    o["admin_mint"] = req("POST", "/api/multica-ui/session", {"X-Session-ID": "sid-fam"}, {})
    # -- who is refused ---------------------------------------------------------------------------------------------
    for who in ("kid", "minor", "guest", "odd"):
        sid = f"sid-{who}"
        o[f"{who}_mint"] = req("POST", "/api/multica-ui/session", {"X-Session-ID": sid}, {})
        o[f"{who}_page"] = req("GET", "/multica/", {"X-Session-ID": sid})
        o[f"{who}_api"] = req("GET", "/multica-api/health", {"X-Session-ID": sid})
        o[f"{who}_login"] = req("POST", "/multica-api/auth/send-code", {"X-Session-ID": sid}, {"email": EMAIL})
    o["bad_session_api"] = req("GET", "/multica-api/health", {"X-Session-ID": "sid-made-up"})
    # -- the cookie: a forge with the real key passes (control); wrong key / purpose / expiry do not ---------------------
    o["forge_real_key"] = req("GET", "/multica-api/health", ck(forge({"k": "multica-ui", "who": "lab"}, SECRET)))
    o["forge_wrong_key"] = req("GET", "/multica-api/health", ck(forge({"k": "multica-ui", "who": "lab"}, "not-the-secret")))
    o["forge_wrong_purpose"] = req("GET", "/multica-api/health", ck(forge({"k": "ma-ui", "who": "lab"}, SECRET)))
    o["forge_expired"] = req("GET", "/multica-api/health", ck(forge({"k": "multica-ui", "who": "lab"}, SECRET, ttl=-5)))
    o["member_cookie_token"] = tok
    return o


def phase_gatedown():
    tok = forge({"k": "multica-ui", "who": "lab"}, SECRET)
    return {"cookie_api": req("GET", "/multica-api/health", ck(tok)), "anon_page": req("GET", "/multica/"),
            "anon_api": req("GET", "/multica-api/health")}


def phase_control():
    return {"anon_api_health": req("GET", "/multica-api/health"), "anon_page": req("GET", "/multica/"),
            "anon_send_code": req("POST", "/multica-api/auth/send-code", {}, {"email": EMAIL}),
            "anon_ws": ws_handshake("/multica-api/ws")}


if __name__ == "__main__":
    print(json.dumps({"main": phase_main, "gatedown": phase_gatedown, "control": phase_control}[sys.argv[1]]()))
