"""A phone, reduced to HTTP over the phone unit's Unix socket (what nginx would forward from the network).
Usage: phonecli.py SOCKET METHOD PATH, with an optional JSON object on stdin: {"cookie": "<zv_device value>",
"body": "<form-urlencoded>"} (secrets never go on the command line). Prints one JSON line {status, body, device}:
`device` is the zv_device cookie the page set (pairing), or null."""

import http.client
import json
import socket
import sys


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("phone.lab.test", timeout=300)
        self.unix_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(300)
        self.sock.connect(self.unix_path)


path, method, url = sys.argv[1], sys.argv[2], sys.argv[3]
raw = sys.stdin.read().strip()
opts = json.loads(raw) if raw else {}
body, cookie = opts.get("body"), opts.get("cookie")
try:
    c = UnixHTTPConnection(path)
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if body is not None else {}
    if cookie:
        headers["Cookie"] = "zv_device=" + cookie
    c.request(method, url, body=body, headers=headers)
    r = c.getresponse()
    device = None
    for value in r.headers.get_all("Set-Cookie") or []:
        name, _, rest = value.partition("=")
        if name.strip() == "zv_device":
            device = rest.split(";", 1)[0]
    print(json.dumps({"status": r.status, "body": r.read().decode("utf-8", "replace"), "device": device}))
except OSError as e:
    print(json.dumps({"status": 0, "body": "connect:" + type(e).__name__, "device": None}))
