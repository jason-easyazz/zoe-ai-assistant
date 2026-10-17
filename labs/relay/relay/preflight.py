#!/usr/bin/env python3
"""Relay preflight: refuse to start frps unless it is multi-tenant by configuration.

frp with NO server plugin allows everything (frp v0.71.0 pkg/plugin/server/manager.go
`handleMutableContent`: `len(plugins) == 0` returns the content unchanged), so a missing
or mistyped [[httpPlugins]] block silently yields an open relay in which any box can
claim any name. A plugin that is configured but down already fails closed per request;
THIS check closes the other half, fail-open per configuration (design section 4.2,
lab cell T2).

Exit codes: 0 = safe to start frps, 1 = configuration unsafe, 2 = plugin unreachable
or not the session master. frps must not be started on any non-zero exit.

Needs Python >= 3.11 (tomllib); the lab image is python:3.12.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
import urllib.error
import urllib.request

REQUIRED_OPS = ("Login", "NewProxy", "NewUserConn")
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _get(cfg: dict, dotted: str, default=None):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _plugin_host_port(addr: str):
    a = addr.strip()
    if a.startswith("https://"):
        return None, None  # an https plugin cannot be reached by this check; treat as unsafe
    if a.startswith("http://"):
        a = a[len("http://"):]
    a = a.split("/", 1)[0]
    host, _, port = a.rpartition(":")
    if not host or not port.isdigit():
        return None, None
    return host, int(port)


def check_config(cfg: dict) -> list[str]:
    """Return a list of problems; empty means the configuration is acceptable."""
    errs: list[str] = []
    plugins = cfg.get("httpPlugins") or []
    if not plugins:
        errs.append("no [[httpPlugins]]: frps would allow every login and every proxy")
    if len(plugins) > 1:
        # frp calls every plugin in turn and a later one may REWRITE the content the session master returned, so a second
        # (permissive, or merely different) plugin is a bypass of the first.
        errs.append(f"exactly one [[httpPlugins]] entry is allowed, found {len(plugins)}")
    covered: set[str] = set()
    for p in plugins:
        host, port = _plugin_host_port(str(p.get("addr", "")))
        if host is None:
            errs.append(f"plugin {p.get('name')!r}: addr {p.get('addr')!r} is not host:port over plain http")
            continue
        if host not in LOOPBACK:
            errs.append(f"plugin {p.get('name')!r}: addr host {host!r} is not loopback (the plugin API is unauthenticated)")
            continue
        if not str(p.get("path", "")).startswith("/"):
            errs.append(f"plugin {p.get('name')!r}: path must start with /")
            continue
        covered.update(p.get("ops") or [])
    for op in REQUIRED_OPS:
        if op not in covered:
            errs.append(f"no loopback plugin covers op {op}")
    if _get(cfg, "transport.tls.force") is not True:
        errs.append("transport.tls.force must be true")
    if not _get(cfg, "transport.tls.certFile") or not _get(cfg, "transport.tls.keyFile"):
        errs.append("transport.tls.certFile/keyFile must be set (frps would otherwise present a random self-signed cert)")
    if not _get(cfg, "vhostHTTPSPort"):
        errs.append("vhostHTTPSPort must be set (the https vhost is the only data path)")
    for key in ("vhostHTTPPort", "tcpmuxHTTPConnectPort", "kcpBindPort", "sshTunnelGateway.bindPort"):
        if _get(cfg, key):
            errs.append(f"{key} must be unset: only the https vhost is allowed")
    if _get(cfg, "subDomainHost"):
        errs.append("subDomainHost must be unset: every name is assigned by the plugin")
    if _get(cfg, "webServer.port") and _get(cfg, "webServer.addr", "0.0.0.0") not in LOOPBACK:
        errs.append("webServer.addr must be loopback when the dashboard is enabled")
    return errs


def probe_plugin(addr: str, path: str, *, timeout: float = 3.0) -> str | None:
    """None if the plugin answers and REJECTS a bogus login; otherwise a problem string."""
    host, port = _plugin_host_port(addr)
    base = f"http://{host}:{port}"
    try:
        with urllib.request.urlopen(base + "/healthz", timeout=timeout) as r:
            if r.status != 200:
                return f"plugin /healthz answered {r.status}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return f"plugin unreachable: {e}"
    bogus = {
        "version": "0.1.0",
        "op": "Login",
        "content": {"user": "preflight0", "metas": {"hid": "preflight0", "secret": "not-a-secret"}},
    }
    req = urllib.request.Request(
        f"{base}{path}?version=0.1.0&op=Login",
        data=json.dumps(bogus).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ans = json.loads(r.read())
    except urllib.error.HTTPError:
        return None  # a non-200 makes frps refuse: fail closed, which is the behaviour we want
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        return f"plugin unreachable: {e}"
    if ans.get("reject") is not True:
        return "plugin ACCEPTED a bogus login: permissive, or not the session master"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--wait-plugin", type=float, default=0.0, help="seconds to wait for the plugin to answer")
    args = ap.parse_args(argv)
    try:
        with open(args.config, "rb") as f:
            cfg = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"preflight: cannot read {args.config}: {e}", file=sys.stderr)
        return 1
    errs = check_config(cfg)
    if errs:
        for e in errs:
            print(f"preflight: CONFIG UNSAFE: {e}", file=sys.stderr)
        return 1
    # config is safe; now require a live, non-permissive plugin on every distinct address
    deadline = time.monotonic() + max(args.wait_plugin, 0.0)
    targets = sorted({(p["addr"], p["path"]) for p in cfg["httpPlugins"]})
    for addr, path in targets:
        while True:
            problem = probe_plugin(addr, path)
            if problem is None:
                break
            if time.monotonic() >= deadline:
                print(f"preflight: PLUGIN NOT READY: {problem}", file=sys.stderr)
                return 2
            time.sleep(0.5)
    print("preflight: ok (plugin configured for Login/NewProxy/NewUserConn, loopback, rejects a bogus login)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
