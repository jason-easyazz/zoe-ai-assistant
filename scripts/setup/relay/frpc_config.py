#!/usr/bin/env python3
"""frpc config generator + safety check for the household box (panel-anywhere sections 2 and 5.4).

`render()` produces the ONLY frpc config a box may run; `check()` is the test that must hold for every
generated config (lab cell T6). The two properties it exists for:

  * the proxy's local target is the nginx RELAY LISTENER and nothing else. A loopback target that is
    not the listener (above all zoe-data's 127.0.0.1:8000, which `_is_internal_request` trusts and which
    fronts every `require_internal_token` endpoint) would publish that service to the whole relay;
  * `transport.tls.trustedCaFile` is set, because frpc silently SKIPS verifying the relay without it
    (frp v0.71.0 pkg/transport/tls.go: InsecureSkipVerify when no CA path is given), so the relay
    secret would go to whoever answers (lab cell T9).

Ported from the F0 relay lab (labs/relay/box/frpc_config.py, PR #2094) for the R1 deployment; the unit tests are
tests/unit/test_relay_frpc_config.py (the lab's fixtures plus the listener-port default). Python >= 3.10: tomllib, or tomli
(pytest depends on it below 3.11), so the check runs in the same slim CI lane as the rest.

CLI (the box runs this BEFORE frpc starts, and refuses to start frpc on a non-zero exit):
    python3 frpc_config.py <frpc.toml> <hid> [relay_addr]
"""
from __future__ import annotations

import ipaddress
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

HID_LEN = (8, 32)
NAME_SUFFIX = ".zoe.internal"
# The nginx relay listener (services/zoe-ui/nginx.d/relay-listener.conf), published to host loopback by docker-compose.yml. The ONLY
# local target frpc may have.
LISTENER_HOST = "127.0.0.1"
LISTENER_PORT = 9443
# zoe-data's loopback port; named so the failure message says what the target would have exposed
ZOE_DATA_PORT = 8000
LOOPBACK_NAMES = {"localhost", "ip6-localhost"}


def own_name(hid: str) -> str:
    return hid + NAME_SUFFIX


def render(
    *,
    hid: str,
    secret: str,
    relay_addr: str,
    relay_port: int,
    relay_ca_file: str,
    relay_server_name: str,
    target_host: str = LISTENER_HOST,
    target_port: int = LISTENER_PORT,
    custom_domains: list[str] | None = None,
    extra_proxy: str = "",
    extra_top: str = "",
    trusted_ca: bool = True,
) -> str:
    """Render frpc.toml. The keyword escape hatches (custom_domains, extra_*, trusted_ca=False) exist only so the
    lab can build its negative fixtures; the box deployment calls render() with the defaults."""
    domains = custom_domains if custom_domains is not None else [own_name(hid)]
    doms = ", ".join(f'"{d}"' for d in domains)
    ca = f'transport.tls.trustedCaFile = "{relay_ca_file}"\n' if trusted_ca else ""
    return (
        f'serverAddr = "{relay_addr}"\n'
        f"serverPort = {relay_port}\n"
        f'user = "{hid}"\n'
        f"loginFailExit = false\n"  # keep retrying while the relay is down or still starting (a box must not give up at boot)
        f'metadatas.hid = "{hid}"\n'
        f'metadatas.secret = "{secret}"\n'
        f"transport.tls.enable = true\n"
        f"{ca}"
        f'transport.tls.serverName = "{relay_server_name}"\n'
        f'log.to = "console"\n'
        f'log.level = "info"\n'
        f"{extra_top}"
        f"\n[[proxies]]\n"
        f'name = "https"\n'
        f'type = "https"\n'
        f"customDomains = [{doms}]\n"
        f'localIP = "{target_host}"\n'
        f"localPort = {target_port}\n"
        f'transport.proxyProtocolVersion = "v2"\n'
        f"{extra_proxy}"
    )


def _is_loopbackish(host: str) -> bool:
    h = host.strip().strip("[]").lower()
    if h in LOOPBACK_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def check(
    text: str,
    *,
    expect_hid: str,
    expect_relay_addr: str | None = None,
    listener: tuple[str, int] = (LISTENER_HOST, LISTENER_PORT),
) -> list[str]:
    """Return the violations in an frpc config; empty means it is acceptable for a household box.

    Fails closed: a config shape the checker did not anticipate is a violation, never an exception that a caller might
    swallow as "no problems"."""
    try:
        return _check(text, expect_hid=expect_hid, expect_relay_addr=expect_relay_addr, listener=listener)
    except Exception as e:  # noqa: BLE001
        return [f"config could not be checked ({type(e).__name__}): refusing it"]


def _table(v) -> bool:
    return isinstance(v, dict)


def _check(text: str, *, expect_hid: str, expect_relay_addr: str | None, listener: tuple[str, int]) -> list[str]:
    errs: list[str] = []
    try:
        cfg = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return [f"not valid TOML: {e}"]

    proxies = cfg.get("proxies", [])
    if not isinstance(proxies, list) or not all(_table(p) for p in proxies):
        return ["proxies must be an array of tables ([[proxies]])"]
    if len(proxies) != 1:
        errs.append(f"exactly one proxy allowed, found {len(proxies)}")
    for key in ("visitors", "includes", "webServer"):
        if key in cfg:
            errs.append(f"{key!r} must not be present (extra proxies / an admin API would bypass this check)")

    transport = cfg.get("transport", {})
    if not _table(transport) or not _table(transport.get("tls", {})):
        return ["transport / transport.tls must be tables"]
    tls = transport.get("tls", {})
    if tls.get("enable") is False:
        errs.append("transport.tls.enable must not be false")
    if not tls.get("trustedCaFile"):
        errs.append("transport.tls.trustedCaFile missing: frpc would not verify the relay")
    if not tls.get("serverName"):
        errs.append("transport.tls.serverName missing")
    proto = transport.get("protocol", "tcp")
    if proto not in ("tcp", "quic"):
        errs.append(f"transport.protocol {proto!r} not allowed")

    if cfg.get("loginFailExit", True) is not False:
        errs.append("loginFailExit must be false: the box has to keep retrying while the relay is down or starting")
    if cfg.get("user") != expect_hid:
        errs.append("user must equal the household id")
    metas = cfg.get("metadatas", {})
    if not _table(metas) or metas.get("hid") != expect_hid or not metas.get("secret"):
        errs.append("metadatas.hid/secret missing or wrong")
    if expect_relay_addr is not None and cfg.get("serverAddr") != expect_relay_addr:
        errs.append(f"serverAddr must be {expect_relay_addr!r}")

    for p in proxies:
        if p.get("type") != "https":
            errs.append(f"proxy type {p.get('type')!r} not allowed: only https")
        if "plugin" in p:  # key presence, not truthiness: an empty [proxies.plugin] table is still a plugin declaration
            errs.append("proxy plugin (e.g. https2http) not allowed: it would terminate TLS in frpc and bypass the relay listener")
        host, port = str(p.get("localIP", "127.0.0.1")), p.get("localPort")
        if (host, port) != listener:
            why = ""
            if port == ZOE_DATA_PORT and _is_loopbackish(host):
                why = " (that is zoe-data's trusted loopback port: it would publish every internal endpoint)"
            elif _is_loopbackish(host):
                why = " (a loopback service other than the relay listener)"
            errs.append(f"proxy target {host}:{port} is not the relay listener {listener[0]}:{listener[1]}{why}")
        ptransport = p.get("transport", {})
        if not _table(ptransport) or ptransport.get("proxyProtocolVersion") != "v2":
            errs.append("proxy transport.proxyProtocolVersion must be v2")
        doms = [str(d).lower().rstrip(".") for d in (p.get("customDomains") or [])]
        if doms != [own_name(expect_hid)]:
            errs.append(f"customDomains must be exactly [{own_name(expect_hid)!r}], found {p.get('customDomains')!r}")
        if p.get("subdomain"):
            errs.append("subdomain must not be set")
    return errs


if __name__ == "__main__":  # python3 frpc_config.py <file> <hid> [relay_addr]
    path, hid = sys.argv[1], sys.argv[2]
    relay = sys.argv[3] if len(sys.argv) > 3 else None
    with open(path, encoding="utf-8") as f:
        problems = check(f.read(), expect_hid=hid, expect_relay_addr=relay)
    for p in problems:
        print("frpc config: " + p, file=sys.stderr)
    sys.exit(1 if problems else 0)
