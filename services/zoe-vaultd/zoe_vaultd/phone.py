"""`zoe-vault-phone`: the three phone pages (credential-vault.md 5.2, 7 and 9), run as their own uid.

nginx routes one path prefix (`/v/`) straight to this unit's Unix socket, bypassing zoe-data, so what a member taps
or types (an approval, a one-time code, Zoe's vault password for U3) never passes through the process that talks to
the model. zoe-data only ever forwards `/v/n/<notice>` links, and a notice id authorises nothing: every answer must come
from a phone PAIRED to the right member, proven by the device cookie this unit set on its own pairing page
(`/v/pair/<token>`, a link only the broker's admin role can mint). This unit keeps no state: it forwards the cookie and
the member's answer to the core, where its uid may use only `card`, `approve`, `otp`, `unlock`, `lock` and
`pair_complete`. Requests are never logged with their bodies, ids or cookies.
"""

from __future__ import annotations

import html
import os
import re
import socketserver
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs

from .broker import request

ROUTE = re.compile(r"^/v/(n|pair)/([A-Za-z0-9_-]{20,80})$")
DEVICE_COOKIE = "zv_device"
MAX_BODY = 4096

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Zoe</title><style>body{{font:16px system-ui;margin:16px;max-width:560px}}button{{font-size:18px;margin:6px 0;
padding:10px 14px;width:100%}}.o{{font-family:monospace;word-break:break-all}}</style></head><body>{body}</body></html>"""


def _card_html(card: dict) -> str:
    rows = []
    for s in card.get("steps", []):
        look = f" (looks like {html.escape(s['looks_like'])})" if s.get("looks_like") else ""
        rows.append(f"<li><b>{html.escape(s['label'])}</b> at <span class=o>{html.escape(s['origin'])}</span>{look}"
                    f"{' - sensitive' if s.get('sensitive') else ''}</li>")
    return (f"<h1>Sign in for {html.escape(card.get('member', ''))}?</h1>"
            f"<p>For: {html.escape(card.get('goal', ''))}</p><ul>{''.join(rows)}</ul>"
            f"<p>Expires in {int(card.get('expires_in', 0))} s.</p>"
            "<form method=post><button name=decision value=approve>Approve</button>"
            "<button name=decision value=deny>Deny</button>"
            "<button name=decision value=deny_lock>Deny and lock the vault</button></form>")


class PhoneHandler(BaseHTTPRequestHandler):
    server_version = "zoe-vault-phone"
    sys_version = ""

    def log_message(self, fmt, *args) -> None:  # never log paths (ids) or bodies
        return

    def _reply(self, status: int, body: str, cookie: str | None = None) -> None:
        data = PAGE.format(body=body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.send_header("X-Frame-Options", "DENY")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _core(self, obj: dict) -> dict:
        return request(self.server.core_socket, obj)  # type: ignore[attr-defined]

    def _route(self):
        m = ROUTE.match(self.path.split("?", 1)[0])
        return (m.group(1), m.group(2)) if m else (None, None)

    def _device(self) -> str | None:
        """The paired-phone secret, from this unit's own HttpOnly cookie (zoe-data never sees it)."""
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == DEVICE_COOKIE and value:
                return value
        return None

    def do_GET(self) -> None:
        kind, ident = self._route()
        if kind == "pair":
            res = self._core({"verb": "pair_complete", "token": ident})
            if not res.get("ok"):
                return self._reply(410, "<p>This pairing link is no longer valid.</p>")
            cookie = (f"{DEVICE_COOKIE}={res['device']}; Path=/v/; Max-Age=31536000; Secure; HttpOnly; "
                      "SameSite=Strict")
            return self._reply(200, f"<p>This phone is now paired for {html.escape(res['member'])}.</p>", cookie)
        if kind != "n":
            return self._reply(404, "<p>Not found.</p>")
        card = self._core({"verb": "card", "notice": ident, "device": self._device()})
        if not card.get("ok"):
            return self._reply(403, "<p>This request is not for this phone, or is no longer open.</p>")
        if card["kind"] == "approve":
            return self._reply(200, _card_html(card))
        if card["kind"] == "otp":
            return self._reply(200, "<h1>Sign-in code</h1><form method=post><input name=code inputmode=numeric "
                                    "autocomplete=one-time-code><button>Send</button></form>")
        return self._reply(200, "<h1>Unlock Zoe's vault</h1><form method=post><input type=password "
                                "name=password autocomplete=current-password><button>Unlock</button></form>")

    def do_POST(self) -> None:
        kind, ident = self._route()
        try:
            n = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            n = -1
        if kind != "n" or n < 0 or n > MAX_BODY:
            return self._reply(400, "<p>Bad request.</p>")
        form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"), max_num_fields=4)
        val = {k: v[0] for k, v in form.items() if v}
        device = self._device()
        card = self._core({"verb": "card", "notice": ident, "device": device})
        if not card.get("ok"):
            val.clear()
            return self._reply(403, f"<p>Refused: {html.escape(str(card.get('error', 'error')))}</p>")
        if card["kind"] == "approve":
            res = self._core({"verb": "approve", "notice": ident, "device": device,
                              "decision": val.get("decision", "")})
        elif card["kind"] == "otp":
            res = self._core({"verb": "otp", "notice": ident, "device": device, "code": val.get("code", "")})
        else:
            res = self._core({"verb": "unlock", "notice": ident, "device": device,
                              "password": val.get("password", "")})
        val.clear()
        if res.get("ok"):
            return self._reply(200, "<p>Done.</p>")
        return self._reply(403, f"<p>Refused: {html.escape(str(res.get('error', 'error')))}</p>")


class PhoneServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, core_socket: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self.core_socket = core_socket
        old = os.umask(0o117)
        try:
            super().__init__(path, PhoneHandler)
        finally:
            os.umask(old)

    def get_request(self):
        conn, _ = super().get_request()
        return conn, ("phone", 0)  # BaseHTTPRequestHandler expects a (host, port) client address
