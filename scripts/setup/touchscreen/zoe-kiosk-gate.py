#!/usr/bin/env python3
"""zoe-kiosk-gate - the panel's own "can't reach Zoe" screen, and the kiosk browser's credential hand-off.

Runs ON THE PANEL, bound to 127.0.0.1 only, Python stdlib only (no venv). The kiosk browser starts here instead of at Zoe
(``start-kiosk.sh``: ``gate_url`` in /opt/TouchKio/config.json):

  GET /        a local status page with three lights - Wi-Fi, link home, Zoe - one plain sentence for whatever is red, and a
               retry that never stops. When Zoe answers, the page goes to /go by itself. Replaces the old boot loop, where an
               unreachable Zoe meant a blank screen and systemd restarting the launcher with no message.
  GET /status  the same as JSON (probes run in a background thread every few seconds, so this never blocks).
  GET /go      hands the browser to Zoe. With the kiosk credential on (``ZOE_KIOSK_CREDENTIAL=1`` here AND
               ``ZOE_PANEL_KIOSK_CREDENTIAL=1`` on zoe-data) it first trades the panel's device token for a single-use ticket
               (``POST /api/panels/<panel>/kiosk-ticket``) and serves a page that POSTs that ticket to
               ``/api/panels/kiosk-enter``; zoe-data sets an HttpOnly cookie and redirects to the estate. The browser never
               sees the device token. Otherwise (or if Zoe says no) it redirects straight to the kiosk URL - today's behaviour.

Guards: loopback bind; the Host header must be this gate (127.0.0.1/localhost on its port), so a DNS-rebinding page cannot
drive it; responses are no-store with a restrictive CSP. Design: docs/architecture/panel-remote-access.md sections 4.2 + 4.4.

Config (environment; the unit reads the voice daemon's env file, then /etc/zoe-kiosk-gate.env):
  ZOE_KIOSK_URL           the estate URL; default: "url" in /opt/TouchKio/config.json (ZOE_KIOSK_CONFIG overrides the path)
  PANEL_ID                default: the kiosk URL's panel_id query parameter
  ZOE_KIOSK_DEVICE_TOKEN  else DEVICE_TOKEN (the voice daemon's); only needed for the credential
  ZOE_KIOSK_CREDENTIAL    1 = do the credential hand-off (default 0)
  VERIFY_SSL              default false, matching the kiosk's --ignore-certificate-errors for the self-signed LAN cert
  ZOE_KIOSK_GATE_PORT     default 7781
"""
from __future__ import annotations

import html
import ipaddress
import json
import logging
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("zoe-kiosk-gate")

DEFAULT_CONFIG = "/opt/TouchKio/config.json"
PROBE_INTERVAL_S = 3.0
READY_INTERVAL_S = 30.0
TAILNET = ipaddress.ip_network("100.64.0.0/10")

SENTENCES = dict(
    wifi_down="No Wi-Fi. The panel isn't connected to a network.",
    wifi_portal="The Wi-Fi wants a sign-in page, which the panel can't complete. Use your phone's hotspot instead.",
    link_login="This panel isn't signed in to the private link home yet.",
    link_down="Can't reach home - Zoe's computer may be off or home internet may be down. I'll keep trying.",
    zoe_down="Zoe isn't answering yet - she may be starting up, or home may be unreachable. I'll keep trying.",
    ready="Connected. Opening Zoe...",
)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def read_kiosk_url(env=os.environ) -> str:
    url = (env.get("ZOE_KIOSK_URL") or "").strip()
    if url:
        return url
    try:
        with open(env.get("ZOE_KIOSK_CONFIG") or DEFAULT_CONFIG, encoding="utf-8") as fh:
            return str(json.load(fh).get("url") or "").strip()
    except Exception:  # noqa: BLE001 - no config = nothing to open; the status page says Zoe is unreachable
        return ""


def is_tailnet_host(host: str) -> bool:
    host = (host or "").strip().lower().rstrip(".")
    if host.endswith(".ts.net"):
        return True
    try:
        return ipaddress.ip_address(host) in TAILNET
    except ValueError:
        return False


def _run(cmd: list[str], timeout: float = 3.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except Exception:  # noqa: BLE001 - a missing tool is "unknown", never a crash
        return ""


def probe_wifi() -> dict:
    """NetworkManager's view: connected or not, and whether the network wants a captive-portal sign-in."""
    out = _run(["nmcli", "-t", "-f", "STATE,CONNECTIVITY", "general"]).strip()
    if not out:
        return dict(state="unknown")
    state, _, connectivity = out.partition(":")
    if not state.startswith("connected"):
        return dict(state="down")
    if connectivity == "portal":
        return dict(state="portal")
    ssid = ""
    for line in _run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"]).splitlines():
        if line.startswith("yes:"):
            ssid = line[4:].replace("\\:", ":")
            break
    return dict(state="up", network=ssid)


def probe_link() -> dict:
    """Tailscale's view (used only when the kiosk URL is a tailnet name or address)."""
    raw = _run(["tailscale", "status", "--json"])
    try:
        data = json.loads(raw)
    except ValueError:
        return dict(state="unknown")
    backend = str(data.get("BackendState") or "")
    if backend == "Running":
        return dict(state="up")
    if backend in ("NeedsLogin", "NeedsMachineAuth"):
        return dict(state="login")
    return dict(state="down", detail=backend)


def _ssl_context(verify: bool):
    if verify:
        return ssl.create_default_context()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_request(url: str, *, method: str = "GET", headers: dict | None = None, verify: bool = False,
                 timeout: float = 4.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method, headers=headers or dict(), data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context(verify)) as resp:
            return resp.status, resp.read(65536)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:  # noqa: BLE001 - unreachable is a status, not a crash
        return 0, b""


class Gate:
    """The state machine behind the page: probe, summarise, hand off. Probes and the HTTP client are injectable for tests."""

    def __init__(self, env=os.environ, *, wifi=probe_wifi, link=probe_link, request=http_request):
        self.kiosk_url = read_kiosk_url(env)
        parts = urllib.parse.urlsplit(self.kiosk_url)
        self.origin = "%s://%s" % (parts.scheme, parts.netloc) if parts.scheme and parts.netloc else ""
        self.next_path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        query = urllib.parse.parse_qs(parts.query)
        self.panel_id = (env.get("PANEL_ID") or (query.get("panel_id") or [""])[0]).strip()
        self.token = (env.get("ZOE_KIOSK_DEVICE_TOKEN") or env.get("DEVICE_TOKEN") or "").strip()
        self.credential = _truthy(env.get("ZOE_KIOSK_CREDENTIAL"))
        self.verify = _truthy(env.get("VERIFY_SSL"))
        self.uses_tailnet = is_tailnet_host(parts.hostname or "")
        self._wifi, self._link, self._request = wifi, link, request
        self._lock = threading.Lock()
        self._snapshot = dict(ready=False, checked_at=0, attempts=0)

    # -- probes -------------------------------------------------------------------------------------------------------------
    def refresh(self) -> dict:
        wifi = self._wifi()
        link = self._link() if self.uses_tailnet else dict(state="not-used")
        status, _ = self._request(self.origin + "/health", verify=self.verify) if self.origin else (0, b"")
        zoe = dict(state="up" if 200 <= status < 300 else "down")
        ready = zoe["state"] == "up"
        if ready:
            key = "ready"
        elif wifi.get("state") == "down":
            key = "wifi_down"
        elif wifi.get("state") == "portal":
            key = "wifi_portal"
        elif link.get("state") == "login":
            key = "link_login"
        elif link.get("state") in ("down", "unknown"):
            key = "link_down"
        else:
            key = "zoe_down"
        with self._lock:
            attempts = self._snapshot.get("attempts", 0) + 1
            self._snapshot = dict(ready=ready, wifi=wifi, link=link, zoe=zoe, message=SENTENCES[key], reason=key,
                                  attempts=attempts, checked_at=int(time.time()), retry_s=PROBE_INTERVAL_S)
            return dict(self._snapshot)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def run_probes_forever(self) -> None:
        while True:
            ready = False
            try:
                ready = bool(self.refresh().get("ready"))
            except Exception as exc:  # noqa: BLE001 - the loop must outlive any one probe
                log.warning("probe failed: %s", type(exc).__name__)
            # Every few seconds while something is wrong; slowly once Zoe answers (the browser is with Zoe by then, and
            # /go re-probes a snapshot older than PROBE_INTERVAL_S before it hands off, so it never acts on a stale "ready").
            time.sleep(READY_INTERVAL_S if ready else PROBE_INTERVAL_S)

    # -- hand-off -----------------------------------------------------------------------------------------------------------
    def handoff(self) -> tuple[int, dict, bytes]:
        """Where /go sends the browser: (status, headers, body)."""
        if not self.kiosk_url:
            return 303, dict(Location="/"), b""
        if not (self.credential and self.token and self.panel_id and self.origin):
            return 303, dict(Location=self.kiosk_url), b""
        url = "%s/api/panels/%s/kiosk-ticket" % (self.origin, urllib.parse.quote(self.panel_id, safe=""))
        status, body = self._request(url, method="POST", headers=dict([("X-Device-Token", self.token)]),
                                     verify=self.verify)
        try:
            data = json.loads(body or b"null") or dict()
        except ValueError:
            data = dict()
        if not isinstance(data, dict):
            data = dict()
        ticket = str(data.get("ticket") or "") if status == 200 else ""
        if not ticket:
            # 404 = the credential is off on zoe-data; 403 = token refused. Either way: today's plain kiosk, and say so.
            log.warning("kiosk ticket not issued (HTTP %s) - opening the kiosk without the credential", status)
            return 303, dict(Location=self.kiosk_url), b""
        enter = self.origin + str(data.get("enter_path") or "/api/panels/kiosk-enter")
        page = FORM_PAGE.replace("@ACTION@", html.escape(enter, quote=True))
        page = page.replace("@TICKET@", html.escape(ticket, quote=True)).replace("@NEXT@", html.escape(self.next_path, quote=True))
        return 200, dict([("Content-Type", "text/html; charset=utf-8"),
                          ("Content-Security-Policy", CSP_BASE + "; form-action " + self.origin)]), page.encode()


CSP_BASE = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'")

FORM_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Zoe</title>
<style>html,body{background:#101418;color:#e8edf2;font:28px system-ui,sans-serif;margin:0;height:100%}
body{display:flex;align-items:center;justify-content:center}</style></head>
<body><p>Opening Zoe...</p>
<form id="enter" method="post" action="@ACTION@"><input type="hidden" name="ticket" value="@TICKET@">
<input type="hidden" name="next" value="@NEXT@"></form>
<script>document.getElementById("enter").submit();</script></body></html>
"""

STATUS_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Zoe - connecting</title>
<style>
html,body{margin:0;height:100%;background:#101418;color:#e8edf2;font-family:system-ui,sans-serif}
main{box-sizing:border-box;height:100%;display:flex;flex-direction:column;justify-content:center;padding:6vh 8vw;gap:4vh}
h1{font-size:44px;font-weight:600;margin:0}
#message{font-size:30px;line-height:1.35;margin:0;max-width:36em}
ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2.4vh}
li{display:flex;align-items:center;gap:20px;font-size:28px}
.dot{width:26px;height:26px;border-radius:50%;background:#58636e;flex:none}
.up .dot{background:#3fb27f} .down .dot{background:#e5534b} .wait .dot{background:#d9a43b}
.detail{color:#9aa7b4;font-size:22px}
#retry{color:#9aa7b4;font-size:22px}
</style></head><body><main>
<h1>Connecting to Zoe</h1>
<p id="message">Checking the connection...</p>
<ul>
<li id="wifi" class="wait"><span class="dot"></span><span>Wi-Fi</span><span class="detail"></span></li>
<li id="link" class="wait"><span class="dot"></span><span>Link home</span><span class="detail"></span></li>
<li id="zoe" class="wait"><span class="dot"></span><span>Zoe</span><span class="detail"></span></li>
</ul>
<p id="retry"></p>
</main>
<script>
(function () {
  var LABELS = {up: "OK", down: "Not connected", portal: "Needs a sign-in page", login: "Not signed in",
                "not-used": "Not used on the home network", unknown: "Checking..."};
  function light(id, state, extra) {
    var li = document.getElementById(id);
    var cls = state === "up" || state === "not-used" ? "up" : (state === "unknown" ? "wait" : "down");
    li.className = cls;
    li.querySelector(".detail").textContent = (LABELS[state] || state || "") + (extra ? " - " + extra : "");
  }
  function tick() {
    fetch("/status", {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (s) {
      if (!s.checked_at) { return; }
      light("wifi", (s.wifi || {}).state, (s.wifi || {}).network);
      light("link", (s.link || {}).state);
      light("zoe", (s.zoe || {}).state);
      document.getElementById("message").textContent = s.message || "";
      if (s.ready) { location.replace("/go"); return; }
      document.getElementById("retry").textContent = "Still trying (attempt " + s.attempts + ") - every few seconds, for as long as it takes.";
    }).catch(function () { });
  }
  tick();
  setInterval(tick, 3000);
})();
</script></body></html>
"""


def make_handler(gate: Gate, port: int):
    allowed_hosts = ("127.0.0.1:%d" % port, "localhost:%d" % port)

    class Handler(BaseHTTPRequestHandler):
        server_version = "zoe-kiosk-gate"
        sys_version = ""

        def log_message(self, fmt, *args):  # noqa: D401 - quiet: one line per request at debug only
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, headers: dict, body: bytes) -> None:
            self.send_response(status)
            base = dict([("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                         ("Referrer-Policy", "no-referrer"), ("Content-Length", str(len(body)))])
            base.update(headers)
            for key, value in base.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - http.server API
            if self.headers.get("Host", "") not in allowed_hosts:
                return self._send(403, dict([("Content-Type", "text/plain")]), b"forbidden")
            path = urllib.parse.urlsplit(self.path).path
            if path == "/":
                return self._send(200, dict([("Content-Type", "text/html; charset=utf-8"),
                                             ("Content-Security-Policy", CSP_BASE + "; form-action 'none'")]),
                                  STATUS_PAGE.encode())
            if path == "/status":
                return self._send(200, dict([("Content-Type", "application/json")]), json.dumps(gate.snapshot()).encode())
            if path == "/go":
                if time.time() - gate.snapshot().get("checked_at", 0) > PROBE_INTERVAL_S:
                    gate.refresh()
                if not gate.snapshot().get("ready"):
                    return self._send(303, dict(Location="/"), b"")
                status, headers, body = gate.handoff()
                return self._send(status, headers, body)
            return self._send(404, dict([("Content-Type", "text/plain")]), b"not found")

        do_HEAD = do_GET

    return Handler


def serve(gate: Gate, port: int) -> ThreadingHTTPServer:
    """Bind 127.0.0.1 only. The Host guard uses the port actually bound (port 0 = any free port, for tests)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    server.RequestHandlerClass = make_handler(gate, server.server_address[1])
    server.daemon_threads = True
    return server


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="zoe-kiosk-gate: %(levelname)s %(message)s")
    port = int(os.environ.get("ZOE_KIOSK_GATE_PORT", "7781"))
    gate = Gate()
    if not gate.kiosk_url:
        log.warning("no kiosk URL (ZOE_KIOSK_URL or config.json url) - the status page will say Zoe is unreachable")
    log.info("serving on 127.0.0.1:%d for %s (credential %s)", port, gate.origin or "?", "on" if gate.credential else "off")
    threading.Thread(target=gate.run_probes_forever, name="probes", daemon=True).start()
    server = serve(gate, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
