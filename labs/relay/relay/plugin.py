#!/usr/bin/env python3
"""Session-master plugin STUB for the F0 relay lab (panel-anywhere section 4.2).

An frps server plugin (HTTP, op in the query string, JSON body) that makes the
relay multi-tenant. frp itself has no tenant model; this is where one household
is stopped from claiming another's name.

Wire contract (read from frp v0.71.0 pkg/plugin/server/{http,types,manager}.go):
  POST /handler?version=0.1.0&op=<Login|NewProxy|NewUserConn|CloseProxy>
  body    {"version","op","content":{...}}
  answer  {"reject":bool,"reject_reason":str,"unchange":bool,"content":{...}}
frps treats a transport error, a non-200 answer or `reject` as a refusal, so this
process fails CLOSED on every path: any exception becomes HTTP 500.

Policy (one registry row per household, see households.json):
  Login        metas.hid + metas.secret must match an ACTIVE row; user == hid;
               pool_count is capped.
  NewProxy     re-authenticates from the login metas (defence in depth); only
               type "https"; proxy name must be "<hid>.https"; any client-named
               domain other than the household's own (a foreign hid, a wildcard,
               an arbitrary host) is REJECTED; everything else is OVERWRITTEN:
               customDomains := ["<hid>.zoe.internal"], subdomain / group / locations /
               headers / ... cleared, bandwidthLimit := the cap with
               bandwidthLimitMode := "server" (client mode is client-enforced,
               i.e. not enforced).
  NewUserConn  suspended household refused; per-household new-connection RATE cap.
               (frp offers no hook when a user connection closes, so a CONCURRENT
               cap cannot be implemented here; see README "Findings".)
  CloseProxy   audit log only.

The household registry stores sha256(secret) only. The lab toggles file
(RELAY_LAB_TOGGLES, honoured only with RELAY_LAB_MODE=1) can switch individual
overwrites off so the lab can run its negative controls. The production plugin
(R2) must not have that hook.

Standard library only, so the same file runs on the relay host unmodified.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HID_RE = re.compile(r"^[a-z0-9]{8,32}$")
NAME_SUFFIX = ".zoe.internal"
DEFAULT_BANDWIDTH = "2441KB"  # 20 Mbit/s (design D10); frp parses KB as 1024 bytes
DEFAULT_RATE_MAX = 64  # new user connections per window (design D10 "64 connections")
DEFAULT_RATE_WINDOW_S = 10.0
DEFAULT_MAX_POOL = 10
MAX_BODY = 1 << 20
KNOWN_OVERWRITES = frozenset({"bandwidth", "domains"})
ROUTE = "/handler"


def canon(name: str) -> str:
    """Canonical form of a host name for comparison: lower-case, no trailing dot."""
    return str(name).strip().lower().rstrip(".")


def own_name(hid: str) -> str:
    return hid + NAME_SUFFIX


def sha256_hex(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class Registry:
    """households.json: {"households": {"<hid>": {"secret_sha256": "...", "active": true}}}."""

    def __init__(self, path: str):
        self.path = path
        self._mtime = None
        self._rows: dict = {}
        self._lock = threading.Lock()

    def _load(self) -> dict:
        with self._lock:
            mtime = os.stat(self.path).st_mtime_ns  # missing file -> exception -> 500 -> fail closed
            if mtime != self._mtime:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._rows = json.load(f).get("households", {})
                self._mtime = mtime
            return self._rows

    def authenticate(self, hid, secret) -> bool:
        """True only for an active household whose secret hashes to the stored value."""
        if not isinstance(hid, str) or not isinstance(secret, str) or not HID_RE.match(hid):
            return False
        row = self._load().get(hid)
        stored = (row or {}).get("secret_sha256", "0" * 64)
        ok = hmac.compare_digest(sha256_hex(secret), str(stored))  # always compare: no hid oracle by timing
        return bool(ok and row is not None and row.get("active", False))

    def is_active(self, hid: str) -> bool:
        row = self._load().get(hid)
        return bool(row and row.get("active", False))


class Policy:
    def __init__(
        self,
        registry: Registry,
        *,
        bandwidth_limit: str = DEFAULT_BANDWIDTH,
        rate_max: int = DEFAULT_RATE_MAX,
        rate_window_s: float = DEFAULT_RATE_WINDOW_S,
        max_pool: int = DEFAULT_MAX_POOL,
        toggles_path: str | None = None,
        lab_mode: bool = False,
        clock=time.monotonic,
    ):
        self.registry = registry
        self.bandwidth_limit = bandwidth_limit
        self.rate_max = rate_max
        self.rate_window_s = rate_window_s
        self.max_pool = max_pool
        self.toggles_path = toggles_path
        self.lab_mode = lab_mode
        self.clock = clock
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    # ---- answers -------------------------------------------------------
    @staticmethod
    def reject(reason: str) -> dict:
        return {"reject": True, "reject_reason": reason, "unchange": True}

    @staticmethod
    def unchanged() -> dict:
        return {"reject": False, "unchange": True}

    @staticmethod
    def changed(content: dict) -> dict:
        return {"reject": False, "unchange": False, "content": content}

    # ---- lab toggles ---------------------------------------------------
    def disabled_overwrites(self) -> frozenset:
        if not (self.lab_mode and self.toggles_path):
            return frozenset()
        try:
            with open(self.toggles_path, "r", encoding="utf-8") as f:
                names = json.load(f).get("disable_overwrite", [])
        except FileNotFoundError:
            return frozenset()
        return frozenset(n for n in names if n in KNOWN_OVERWRITES)

    # ---- ops -----------------------------------------------------------
    def handle(self, op: str, content) -> dict:
        if not isinstance(content, dict):
            return self.reject("malformed request")
        if op == "Login":
            return self._login(content)
        if op == "NewProxy":
            return self._new_proxy(content)
        if op == "NewUserConn":
            return self._new_user_conn(content)
        if op == "CloseProxy":
            user = content.get("user") or {}
            _log("close-proxy", hid=(user.get("metas") or {}).get("hid"), proxy=content.get("proxy_name"))
            return self.unchanged()
        # ops this relay does not use: refuse rather than wave through
        return self.reject("operation not supported")

    def _login(self, c: dict) -> dict:
        metas = c.get("metas") or {}
        hid = metas.get("hid")
        if not self.registry.authenticate(hid, metas.get("secret")):
            _log("login-refused", hid=_safe_hid(hid))
            return self.reject("login refused")
        if c.get("user") != hid:
            _log("login-refused", hid=hid, why="user!=hid")
            return self.reject("login refused")
        if int(c.get("pool_count") or 0) > self.max_pool:
            _log("login-refused", hid=hid, why="pool_count")
            return self.reject("login refused")
        _log("login-ok", hid=hid)
        return self.unchanged()

    def _authenticated_hid(self, c: dict):
        user = c.get("user") or {}
        metas = user.get("metas") or {}
        hid = metas.get("hid")
        if self.registry.authenticate(hid, metas.get("secret")) and user.get("user") == hid:
            return hid
        return None

    def _new_proxy(self, c: dict) -> dict:
        hid = self._authenticated_hid(c)
        if hid is None:
            return self.reject("proxy refused")
        ptype = c.get("proxy_type")
        if ptype != "https":
            _log("proxy-refused", hid=hid, why="type", type=_clip(ptype))
            return self.reject("proxy type not allowed: only https")
        if c.get("proxy_name") != hid + ".https":
            _log("proxy-refused", hid=hid, why="name", name=_clip(c.get("proxy_name")))
            return self.reject("proxy name not allowed")
        own = own_name(hid)
        for d in c.get("custom_domains") or []:
            if canon(d) != own:
                _log("proxy-refused", hid=hid, why="foreign-or-wildcard-name", name=_clip(d))
                return self.reject("foreign or wildcard name refused")
        off = self.disabled_overwrites()
        out = dict(c)
        if "domains" not in off:
            out["custom_domains"] = [own]
            for k in ("subdomain", "group", "group_key", "http_user", "http_pwd", "host_header_rewrite",
                      "route_by_http_user", "sk", "multiplexer"):
                out[k] = ""
            out["locations"] = []
            out["headers"] = {}
            out["response_headers"] = {}
            out["allow_users"] = []
            out["remote_port"] = 0
            out["metas"] = {}
            out["annotations"] = {}
            out["use_compression"] = False
        if "bandwidth" not in off:
            out["bandwidth_limit"] = self.bandwidth_limit
            out["bandwidth_limit_mode"] = "server"
        _log("proxy-ok", hid=hid, domains=out.get("custom_domains"), bw=out.get("bandwidth_limit"),
             mode=out.get("bandwidth_limit_mode"), overwrites_off=sorted(off) or None)
        return self.changed(out)

    def _new_user_conn(self, c: dict) -> dict:
        user = c.get("user") or {}
        hid = (user.get("metas") or {}).get("hid")
        if not isinstance(hid, str) or not HID_RE.match(hid) or not self.registry.is_active(hid):
            _log("conn-refused", hid=_safe_hid(hid), why="suspended-or-unknown")
            return self.reject("household suspended")
        now = self.clock()
        with self._lock:
            q = self._hits[hid]
            while q and now - q[0] > self.rate_window_s:
                q.popleft()
            if len(q) >= self.rate_max:
                _log("conn-refused", hid=hid, why="rate")
                return self.reject("connection rate limit")
            q.append(now)
        return self.unchanged()


def _safe_hid(hid) -> str:
    return hid if isinstance(hid, str) and HID_RE.match(hid) else "?"


def _clip(v, n=80):
    return str(v)[:n]


def _log(event: str, **kv) -> None:
    kv = {k: v for k, v in kv.items() if v is not None}
    sys.stderr.write("plugin " + event + " " + json.dumps(kv, sort_keys=True) + "\n")
    sys.stderr.flush()


def make_handler(policy: Policy):
    class Handler(BaseHTTPRequestHandler):
        server_version = "relay-plugin-stub"
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):  # request lines go through _log; never log bodies (they carry secrets)
            pass

        def _send(self, code: int, obj: dict) -> None:
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if urlparse(self.path).path == "/healthz":
                self._send(200, {"service": "relay-plugin-stub", "ready": True})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                u = urlparse(self.path)
                if u.path != ROUTE:
                    return self._send(404, {"error": "not found"})
                n = int(self.headers.get("Content-Length", "-1"))
                if n < 0 or n > MAX_BODY:
                    return self._send(400, {"error": "bad length"})
                body = json.loads(self.rfile.read(n))
                op = (parse_qs(u.query).get("op") or [""])[0]
                if not op or body.get("op") != op:
                    return self._send(400, {"error": "op mismatch"})
                self._send(200, policy.handle(op, body.get("content")))
            except Exception as e:  # noqa: BLE001 - every failure must read as a refusal to frps
                _log("error", err=type(e).__name__)
                try:
                    self._send(500, {"error": "internal"})
                except Exception:  # noqa: BLE001
                    pass

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", default=os.environ.get("RELAY_REGISTRY", "/etc/relay/households.json"))
    ap.add_argument("--listen", default=os.environ.get("RELAY_PLUGIN_LISTEN", "127.0.0.1:9100"))
    ap.add_argument("--bandwidth", default=os.environ.get("RELAY_BANDWIDTH_LIMIT", DEFAULT_BANDWIDTH))
    ap.add_argument("--rate-max", type=int, default=int(os.environ.get("RELAY_RATE_MAX", DEFAULT_RATE_MAX)))
    ap.add_argument("--rate-window", type=float, default=float(os.environ.get("RELAY_RATE_WINDOW_S", DEFAULT_RATE_WINDOW_S)))
    args = ap.parse_args(argv)
    host, _, port = args.listen.rpartition(":")
    if host not in ("127.0.0.1", "::1", "localhost"):
        sys.stderr.write("refusing to listen on a non-loopback address: the plugin API is unauthenticated\n")
        return 2
    lab = os.environ.get("RELAY_LAB_MODE") == "1"
    policy = Policy(
        Registry(args.registry),
        bandwidth_limit=args.bandwidth,
        rate_max=args.rate_max,
        rate_window_s=args.rate_window,
        toggles_path=os.environ.get("RELAY_LAB_TOGGLES"),
        lab_mode=lab,
    )
    if lab:
        _log("lab-mode", note="overwrite toggles are honoured; never set RELAY_LAB_MODE in production")
    srv = ThreadingHTTPServer((host, int(port)), make_handler(policy))
    _log("listening", addr=args.listen, bandwidth=args.bandwidth, rate_max=args.rate_max)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
