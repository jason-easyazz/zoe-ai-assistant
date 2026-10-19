"""One request to a Unix socket, as whatever uid runs this (the lab runs it as zoe-svc, lab-grouped, ...).
REQUEST may be "-" to read it from stdin. Prints exactly one JSON line: the broker's reply, or {"ok": false, "error": "<connect error>"}."""

import json
import socket
import sys

path, req = sys.argv[1], sys.argv[2]
if req == "-":
    req = sys.stdin.read().strip()   # a request carrying a password never goes on the command line
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(600)
    s.connect(path)
    s.sendall(req.encode() + b"\n")
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    print(buf.decode().strip() or json.dumps({"ok": False, "error": "no_reply"}))
except OSError as e:
    print(json.dumps({"ok": False, "error": "connect:" + type(e).__name__}))
