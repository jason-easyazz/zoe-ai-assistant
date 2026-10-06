"""In-process egress audit for the bake-off's G0 gate (zero non-loopback connects). strace is not installed on this box.

The window puts this directory on the server's python path and sets EGRESS_AUDIT_LOG to a file. Every socket connect and DNS lookup made
by any Python thread of the process is appended to the log; a non-loopback target is a line that says VIOLATION. The first line,
hook-loaded, proves the hook was imported (a log that does not exist means it never was).

WHY uvloop IS BLOCKED. Run 1 (2026-10-06) wrote NO log: the Hindsight server runs on uvloop, whose transports (asyncpg, httpx, the OpenAI
client) connect in C through libuv and never raise the socket.connect audit event, so the hook saw nothing and the gate read "not measured".
Making the uvloop import fail sends the server (hindsight_api.main: "uvloop not installed, using default asyncio event loop") down the
stock asyncio path, where every connect is a Python socket.connect the hook can see. The cost is a slower event loop on a server that is
bound by a 4B-parameter model; the gain is an instrument that is not blind. Verified: with uvloop installed the hook logs nothing for a
connect, without it the same connect is logged (tests/unit/test_zmb_bakeoff.py runs the hook in a subprocess).

It does not see connects made by native extensions that open sockets in C outside the Python socket module (none expected in this
dependency set); the sampler's ss poll in bakeoff_measure.py is the second, independent look.
"""
import ipaddress
import os
import sys
import threading
import time

_LOG = os.environ.get("EGRESS_AUDIT_LOG", "/home/zoe/.zoe/bakeoff-2026-10/egress-audit.log")
_lock = threading.Lock()
sys.modules["uvloop"] = None            # `import uvloop` now raises ImportError: the audit hook can see every asyncio connect


def _loop(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


def _w(tag, msg):
    with _lock:
        with open(_LOG, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} pid={os.getpid()} {tag} {msg}\n")


def _hook(event, args):
    try:
        if event == "socket.connect":
            addr = args[1]
            if isinstance(addr, tuple) and addr:
                _w("ok" if _loop(addr[0]) else "VIOLATION", f"connect {addr!r}")
            elif isinstance(addr, str):
                _w("ok", f"connect unix:{addr}")
        elif event == "socket.getaddrinfo":
            host = args[0]
            if isinstance(host, (str, bytes)):
                h = host.decode() if isinstance(host, bytes) else host
                _w("ok" if _loop(h) else "VIOLATION", f"getaddrinfo {h}")
    except Exception:
        pass


sys.addaudithook(_hook)
_w("ok", "hook-loaded uvloop=blocked")
