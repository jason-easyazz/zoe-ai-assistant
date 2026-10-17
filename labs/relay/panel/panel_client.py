#!/usr/bin/env python3
"""Lab "panel" client: what the voice daemon does (pinned `requests`) and what the kiosk gate does (link probe).

  get    GET https://<name>/<path> with verify=<household CA> (pinned) or the system store, optionally a client cert.
         Prints one JSON line: {"ok":bool,"status":int|null,"body":str,"elapsed_s":float,"error":str|null}
  probe  the T7 "link home" classifier (panel-anywhere section 6.4). Prints one JSON line {"state": ...}:
           relay_unreachable  no TCP connection (relay down, blocked network)
           home_offline       TCP ok, the relay answered the ClientHello with a TLS alert instead of a
                              ServerHello (frps `vhostFailed`: no box has registered this name)
           home_unresponsive  TCP ok, nothing usable came back before the timeout / the relay closed silently
           cert_untrusted     handshake reached the box but its certificate does not verify against the pinned CA
           green              handshake complete, server certificate verified, client certificate accepted
           other              anything else (detail carries the error)

Python >= 3.10; `requests` is only needed for `get`.
"""
from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import time


def _emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True))


def cmd_get(a) -> int:
    import requests  # imported here so `probe` has no third-party dependency

    verify = a.cacert if a.cacert else True  # True = the requests default bundle (REQUESTS_CA_BUNDLE for the system store)
    kw = {"verify": verify, "timeout": a.timeout, "headers": {k.strip(): v.strip() for k, v in (h.split(":", 1) for h in (a.header or []))}}
    if a.cert:
        kw["cert"] = (a.cert, a.key)
    t0 = time.monotonic()
    try:
        r = requests.get(a.url, **kw)
        _emit({"ok": True, "status": r.status_code, "body": r.text if not a.no_body else f"<{len(r.content)} bytes>",
               "bytes": len(r.content), "elapsed_s": round(time.monotonic() - t0, 3), "error": None})
    except Exception as e:  # noqa: BLE001 - the lab wants the exception class and text, whatever it is
        _emit({"ok": False, "status": None, "body": "", "elapsed_s": round(time.monotonic() - t0, 3),
               "error": f"{type(e).__name__}: {e}"})
    return 0


def classify_tls_error(e: BaseException) -> str:
    text = f"{type(e).__name__}: {e}".lower()
    if isinstance(e, ssl.SSLCertVerificationError) or "certificate verify failed" in text:
        return "cert_untrusted"
    if any(k in text for k in ("unrecognized name", "unrecognized_name", "internal error", "internal_error",
                               "handshake failure", "handshake_failure", "no application protocol")):
        return "home_offline"  # the relay answered the ClientHello with a TLS alert instead of a ServerHello
    return "other"


def cmd_probe(a) -> int:
    t0 = time.monotonic()
    try:
        sock = socket.create_connection((a.host, a.port), timeout=a.timeout)
    except OSError as e:
        _emit({"state": "relay_unreachable", "detail": f"{type(e).__name__}: {e}", "elapsed_s": round(time.monotonic() - t0, 3)})
        return 0
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.load_verify_locations(a.cacert)
        if a.cert:
            ctx.load_cert_chain(a.cert, a.key)
        sock.settimeout(a.timeout)
        with ctx.wrap_socket(sock, server_hostname=a.sni) as tls:
            # TLS 1.3 + mTLS: a client-cert rejection only surfaces on the first read, so do one request
            tls.sendall(f"GET /lab/whoami HTTP/1.1\r\nHost: {a.sni}\r\nConnection: close\r\n\r\n".encode())
            first = tls.recv(64)
            _emit({"state": "green", "detail": first.split(b"\r\n", 1)[0].decode("latin1"),
                   "elapsed_s": round(time.monotonic() - t0, 3)})
    except (socket.timeout, TimeoutError) as e:
        _emit({"state": "home_unresponsive", "detail": f"{type(e).__name__}: {e}", "elapsed_s": round(time.monotonic() - t0, 3)})
    except (ssl.SSLEOFError, ConnectionResetError, BrokenPipeError) as e:
        _emit({"state": "home_unresponsive", "detail": f"{type(e).__name__}: {e}", "elapsed_s": round(time.monotonic() - t0, 3)})
    except ssl.SSLError as e:
        _emit({"state": classify_tls_error(e), "detail": f"{type(e).__name__}: {e}", "elapsed_s": round(time.monotonic() - t0, 3)})
    except OSError as e:
        _emit({"state": "other", "detail": f"{type(e).__name__}: {e}", "elapsed_s": round(time.monotonic() - t0, 3)})
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get")
    g.add_argument("url")
    g.add_argument("--cacert", help="pin to this CA (verify=<ca>); omit to use the default bundle")
    g.add_argument("--cert")
    g.add_argument("--key")
    g.add_argument("--header", action="append")
    g.add_argument("--timeout", type=float, default=15.0)
    g.add_argument("--no-body", action="store_true")
    g.set_defaults(fn=cmd_get)
    p = sub.add_parser("probe")
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=443)
    p.add_argument("--sni", required=True)
    p.add_argument("--cacert", required=True)
    p.add_argument("--cert")
    p.add_argument("--key")
    p.add_argument("--timeout", type=float, default=4.0)
    p.set_defaults(fn=cmd_probe)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
