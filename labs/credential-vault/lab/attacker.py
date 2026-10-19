"""Cell V17's attacker, run as zoe-data's uid: try every way to reach the login browser's DevTools and, if one
works, read the filled password (the page's inputs, and the sign-in POST body). Prints one JSON line; it reports
only WHETHER the run's canary marker was seen, never a value.
Usage: attacker.py MARKER UNIX_SOCKET TCP_PORT SECONDS [SHARED_SOCKET]"""

import json
import os
import socket
import sys
import time

marker, unix_path, port, seconds = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
shared = sys.argv[5] if len(sys.argv) > 5 else None
out = {"unix": None, "tcp": None, "shared": None, "attached": False, "found_canary": False, "how": None}
deadline = time.monotonic() + seconds

# 1. the broker's private DevTools socket
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(unix_path)
    s.sendall(json.dumps({"id": 1, "method": "Target.getTargets"}).encode() + b"\0")
    s.settimeout(3)
    data = s.recv(65536)
    out["unix"] = "connected, got %d bytes" % len(data) if data else "connected, closed by the launcher"
    s.close()
except OSError as e:
    out["unix"] = type(e).__name__

# 2. a TCP DevTools port on loopback
try:
    socket.create_connection(("127.0.0.1", port), timeout=2).close()
    out["tcp"] = "open"
except OSError as e:
    out["tcp"] = type(e).__name__

# 3. (negative control only) a shared DevTools endpoint: drive the core's own sessions, learned from its events
if shared:
    s = None
    while time.monotonic() < deadline and s is None:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(shared)
        except OSError:
            s = None
            time.sleep(0.05)
    out["shared"] = "connected" if s else "never available"
    if s is not None:
        s.settimeout(0.1)
        sessions, enabled, buf, mid = set(), set(), b"", 100
        while time.monotonic() < deadline and not out["found_canary"]:
            try:
                chunk = s.recv(1 << 16)
                if not chunk:
                    break
                buf += chunk
            except socket.timeout:
                pass
            except OSError:
                break
            while b"\0" in buf:
                raw, buf = buf.split(b"\0", 1)
                if marker.encode() in raw:
                    out["found_canary"] = True
                    out["how"] = "postData" if b"requestWillBeSent" in raw else "input value"
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("sessionId"):
                    sessions.add(msg["sessionId"])
            for sid in list(sessions):
                out["attached"] = True
                try:
                    if sid not in enabled:
                        mid += 1
                        s.sendall(json.dumps({"id": mid, "sessionId": sid, "method": "Network.enable"}).encode() + b"\0")
                        enabled.add(sid)
                    mid += 1
                    s.sendall(json.dumps({"id": mid, "sessionId": sid, "method": "Runtime.evaluate", "params": {
                        "expression": "Array.from(document.querySelectorAll('input')).map(e => e.value).join('|')",
                        "returnByValue": True}}).encode() + b"\0")
                except OSError:
                    pass
        s.close()
print(json.dumps(out), flush=True)
os._exit(0)
